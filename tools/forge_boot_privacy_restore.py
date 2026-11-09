"""Restore the original fstab only after journaled cleanup; no remount/reboot."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

from forge_boot_privacy import private_fstab
from forge_recovery_bootplan import digest
from forge_recovery_cleanup import inputs as cleanup_inputs
from forge_recovery_stage_dispatch import PHASES, MODULES
from forge_target_journal import locked, events, read_record, write_record, append_event


def validate_privacy(plan, pin, clean):
    if (digest(plan) != pin or plan['host'] != clean['host'] or
            plan['before']['machine_id'] != clean['machine_id'] or
            [v['path'] for v in plan['before']['files']] != ['/etc/fstab'] or
            [v['path'] for v in plan['after']['files']] != ['/etc/fstab']):
        raise ValueError('Original privacy journal differs from cleanup target')
    old, new = plan['before']['files'][0], plan['after']['files'][0]
    if old['kind'] != 'file' or new['kind'] != 'file': raise ValueError('Expected fstab file preimages')
    text = base64.b64decode(old['data'], validate=True).decode()
    entries = [line.split('#', 1)[0].split() for line in text.splitlines()]
    entries = [v for v in entries if len(v) >= 2 and v[1] == '/boot/firmware']
    if (len(entries) != 1 or private_fstab(text, entries[0][0]) != base64.b64decode(new['data'], validate=True).decode()
            or new['sha256'] != clean['image_plan']['fstab_sha256'] or old['sha256'] == new['sha256']
            or any(old[k] != new[k] for k in ('mode', 'uid', 'gid', 'xattrs', 'atime_ns'))):
        raise ValueError('Journal is not the exact original-to-private fstab change')


def applied(history, plan):
    if len(history) != 2: raise ValueError('Require exactly one acknowledged privacy application; never replay restore')
    first, last = history
    ack = last.get('result', {})
    if (first.get('state') != 'dispatch' or last.get('state') != 'acknowledged' or
            any(v.get('direction') != 'apply' or v.get('nonce') != ack.get('nonce') for v in history) or
            ack.get('direction') != 'apply' or ack.get('machine_id') != plan['before']['machine_id'] or
            [v.get('path') for v in ack.get('files', [])] != ['/etc/fstab'] or
            any(v.get('status') not in ('applied', 'already-applied') for v in ack['files'])):
        raise ValueError('Original privacy application is not acknowledged')


def inputs(staging, staging_pin, boot_id, journal, pin):
    clean = cleanup_inputs(staging, staging_pin, boot_id)
    if not clean['image_removed'] or clean['completed_phases'] != list(reversed(PHASES)):
        raise ValueError('Complete staging restoration and owned image removal first')
    journal = Path(journal).absolute()
    with locked(journal) as (fd, plan):
        validate_privacy(plan, pin, clean)
        applied(events(fd), plan)
        if 'privacy-restore-attempt.json' in os.listdir(fd):
            raise ValueError('Privacy restore already attempted; inspect without replay')
    return dict(cleanup=clean, journal=str(journal), plan=plan, plan_sha256=pin)


BOOTSTRAP = '''import json,sys,types,subprocess
r=json.load(sys.stdin)
for name,source in r['modules']:
    m=types.ModuleType(name);sys.modules[name]=m
    exec(compile(source,'<privacy-restore:'+name+'>','exec'),m.__dict__)
from forge_boot_observation import read_boot,normal_boot
from forge_boot_privacy import MOUNT_CHECK,verify_mount
from forge_boot_artifacts import inventory
from forge_target_files import parent_fd,snapshot,equivalent
from forge_target_ssh import perform
clean=r['clean']; baseline=[]
def guard():
    boot=normal_boot(read_boot(),clean['machine_id'])
    if boot['boot_id']!=clean['boot_id']: raise ValueError('Approved normal boot changed')
    roots=lambda text:[t for t in text.split() if t.startswith('root=')]
    if roots(boot['cmdline'])!=roots(clean['original_boot']['cmdline']): raise ValueError('Native root changed')
    mount=json.loads(subprocess.check_output([sys.executable,'-c',MOUNT_CHECK],stdin=subprocess.DEVNULL,text=True,timeout=30))
    verify_mount(mount,clean['image_plan']['boot_source'],True)
    for wanted in clean['before']['files']:
        with parent_fd(wanted['path']) as (fd,name):
            if not equivalent(snapshot(fd,name,wanted['path']),wanted): raise ValueError('Restored boot preimages changed')
    current=inventory('/boot/firmware',[clean['image_plan']['sha256']])
    if baseline and current!=baseline[0]: raise ValueError('Boot inventory changed across fstab restore')
    baseline.append(current)
    if read_boot()!=boot: raise ValueError('Boot changed during inventory')
ack=perform(r['plan'],'restore',r['nonce'],guard=guard)
print(json.dumps(dict(acknowledgement=ack,inventory=baseline[0],boot_id=clean['boot_id'])))
'''


def transport(frozen, nonce):
    base = Path(__file__).parent
    names = (*MODULES, 'forge_boot_artifacts')
    request = dict(clean=frozen['cleanup'], plan=frozen['plan'], nonce=nonce,
                   modules=[(name, (base/(name+'.py')).read_text()) for name in names])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', frozen['plan']['host'], 'sudo -n python3 -c '+shlex.quote(BOOTSTRAP)],
        input=json.dumps(request), text=True, capture_output=True, timeout=300, check=True)
    return json.loads(result.stdout)


def restore(frozen):
    clean = frozen['cleanup']
    if inputs(clean['staging'], clean['staging_sha256'], clean['boot_id'], frozen['journal'], frozen['plan_sha256']) != frozen:
        raise ValueError('Privacy restoration inputs changed after owner review')
    with locked(frozen['journal']) as (fd, plan):
        validate_privacy(plan, frozen['plan_sha256'], clean)
        applied(events(fd), plan)
        nonce = uuid.uuid4().hex
        write_record(fd, 'privacy-restore-attempt.json', dict(request=frozen, nonce=nonce))
        intent = dict(state='dispatch', direction='restore', nonce=nonce)
        append_event(fd, intent)
        try:
            observed = transport(frozen, nonce)
            ack = observed['acknowledgement']
            if (ack.get('nonce') != nonce or ack.get('direction') != 'restore' or
                    ack.get('machine_id') != clean['machine_id'] or
                    [v.get('path') for v in ack.get('files', [])] != ['/etc/fstab'] or
                    any(v.get('status') not in ('applied', 'already-applied') for v in ack['files']) or
                    observed.get('boot_id') != clean['boot_id'] or not isinstance(observed.get('inventory'), list)):
                raise ValueError('Invalid guarded privacy restoration acknowledgement')
            write_record(fd, 'privacy-restore-observation.json', observed)
            append_event(fd, dict(intent, state='acknowledged', result=ack))
            result = dict(status='original-fstab-restored-awaiting-fresh-mount', plan_sha256=frozen['plan_sha256'],
                boot_id=clean['boot_id'], private_mount_retained=True, original_mount_verified=False,
                reboot_performed=False, automatic_retry_performed=False,
                inventory_sha256=digest(observed['inventory']))
            write_record(fd, 'privacy-restore-acceptance.json', result)
            return result
        except BaseException as exc:
            append_event(fd, dict(intent, state='uncertain', error=type(exc).__name__))
            raise
