"""Owner-operated, resumable healthy watchdog trial with backup and restore.

Each step exits without polling or repeating a reboot. Inspect retained evidence
before advancing. No failed kernel, watchdog expiry, or EEPROM write is induced.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid

from forge_boot_observation import capture as boot_capture, verify as boot_verify
from forge_target_backup import capture
from forge_target_files import equivalent
from forge_target_journal import private_directory, write_record
from forge_target_ssh import dispatch
from forge_tryboot_recipe import PATHS, prepare_watchdog_staging
from forge_watchdog_handoff import verify
from validate_recovery_fence import remote


WATCHDOG = '''import json,os
from pathlib import Path
p=Path('/sys/class/watchdog/watchdog0')
r={n:(p/n).read_text().strip() for n in ('identity','state','timeout')}
r['open_timeout']=Path('/sys/module/watchdog/parameters/open_timeout').read_text().strip()
r['early_watchdog']=Path('/proc/device-tree/soc/watchdog@7e100000/early-watchdog').is_file()
owners=[]
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit(): continue
 try:
  for fd in (proc/'fd').iterdir():
   try: target=os.readlink(fd)
   except FileNotFoundError: continue
   if target in ('/dev/watchdog','/dev/watchdog0'):
    owners.append(int(proc.name));break
 except FileNotFoundError: continue
r['owner_pids']=sorted(owners)
print(json.dumps(r))
'''


def step(directory, action, host, *, firmware_revision=None, physical_recovery_available=False):
    if firmware_revision is not None and action != 'prepare':
        raise ValueError('Firmware revision is frozen at prepare time')
    directory = Path(directory).absolute()
    if action == 'prepare':
        directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    def save(name, value):
        write_record(fd, name + '.json', value)
    def read(name):
        from forge_target_journal import read_record
        return read_record(fd, name + '.json')
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action == 'prepare':
            watchdog = json.loads(remote(host, WATCHDOG).stdout)
            if watchdog['owner_pids'] != [1] or watchdog['state'] != 'active':
                raise RuntimeError('Native watchdog must already be active and owned by PID 1')
            save('watchdog-before', watchdog)
            nonce = uuid.uuid4().hex
            from forge_trial_firmware import paths, fetch_pair, prepare as prepare_firmware
            selected = PATHS if firmware_revision is None else paths(nonce)
            capture(host, selected, directory / 'before.json')
            save('boot-before', boot_capture(host))
            if firmware_revision is None:
                review = prepare_watchdog_staging(directory / 'plans', host, read('before'), nonce)
            else:
                bundle = fetch_pair(firmware_revision)
                save('firmware-bundle', bundle)
                review = prepare_firmware(directory / 'plans', host, read('before'), nonce, bundle)
            save('review', dict(review, host=host))
        else:
            review = read('review')
            if review['host'] != host:
                raise ValueError('Target differs from prepared trial')
            if (review['kind'] == 'alternate-firmware-watchdog-trial'
                    and action in ('apply', 'reboot-trial') and not physical_recovery_available):
                raise PermissionError('Alternate firmware trial requires confirmed physical power-cycle recovery')
            if action in ('apply', 'restore'):
                phases = review['phases'] if action == 'apply' else list(reversed(review['phases']))
                for phase in phases:
                    dispatch(phase['journal'], action, approved_plan_sha256=phase['plan_sha256'])
                save(action + '-complete', {'status': 'acknowledged'})
            elif action in ('reboot-trial', 'reboot-normal'):
                trial = action == 'reboot-trial'
                read('apply-complete' if trial else 'restore-complete')
                save(action + '-intent', {'host': host, 'trial': trial})
                result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                                         'sudo -n reboot "0 tryboot"' if trial else 'sudo -n reboot'],
                                        capture_output=True, text=True, timeout=20)
                save(action + '-transport', {'returncode': result.returncode})
            elif action == 'observe':
                observed = boot_capture(host)
                watchdog = json.loads(remote(host, WATCHDOG).stdout)
                save('trial-observation', {'boot': observed, 'watchdog': watchdog})
                try:
                    result = verify(observed, read('boot-before'), review['machine_id'], review['nonce'],
                                    review['watchdog_timeout'], watchdog)
                except ValueError as exc:
                    save('handoff-rejection', {'status': 'unqualified', 'reason': str(exc)})
                    raise
                save('handoff-verification', result)
            elif action == 'finish':
                normal = boot_capture(host)
                result = boot_verify(normal, read('trial-observation')['boot'], review['machine_id'])
                capture(host, review.get('guarded_paths', PATHS), directory / 'after.json')
                if not all(equivalent(a, b) for a, b in zip(read('before')['files'], read('after')['files'])):
                    raise RuntimeError('Restored boot files differ from backup')
                save('normal-verification', result)
                save('watchdog-restored', json.loads(remote(host, WATCHDOG).stdout))
            else:
                raise ValueError('Unknown trial step')
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare','apply','reboot-trial','observe','restore','reboot-normal','finish'))
    parser.add_argument('--host', required=True)
    parser.add_argument('--directory', required=True, type=Path)
    parser.add_argument('--firmware-revision', help='Prepare only: exact upstream firmware commit')
    parser.add_argument('--physical-recovery-available', action='store_true',
                        help='Owner confirms physical power-cycle recovery is available for this trial')
    args = parser.parse_args()
    step(args.directory, args.action, args.host, firmware_revision=args.firmware_revision,
         physical_recovery_available=args.physical_recovery_available)
    print(args.action + ': completed')
