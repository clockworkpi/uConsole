"""Qualify an abrupt thermal fault through a real guest and shared controller.

Only a new disposable overlay is used. Guest I2C access deliberately enables
the modeled protection bit; no SSH or physical-target access is performed.
Retain the dirty overlay and failed-job evidence rather than claiming clean stop.
"""
import argparse
import json
from pathlib import Path
import socket
import tempfile
import time
from unittest.mock import patch

from forge_controller import Controller
from forge_workspace import sha256
from validate_desktop_preparation_gui import fixture
import uconsole_emulator


ENABLE = '''set -e
mountpoint -q /proc || mount -t proc proc /proc
mountpoint -q /sys || mount -t sysfs sysfs /sys
mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev
modprobe i2c_bcm2835
modprobe axp20x_i2c
modprobe i2c_dev
python3 - <<'PY'
import fcntl,json,os
from pathlib import Path
devices = [p for p in Path('/sys/bus/i2c/devices').glob('*-0034')
           if (p/'of_node/compatible').is_file()
           and b'x-powers,axp221' in (p/'of_node/compatible').read_bytes().split(bytes([0]))]
assert len(devices) == 1, devices
bus = int(devices[0].name.split('-')[0])
fd = os.open('/dev/i2c-' + str(bus), os.O_RDWR)
try:
    # Deliberate register-level fault qualification in this disposable VM.
    # The guest MFD owns the address; do not use this on physical hardware.
    fcntl.ioctl(fd, 0x0706, 0x34)
    os.write(fd, bytes([0x8f]))
    before = os.read(fd, 1)[0]
    os.write(fd, bytes([0x8f, before | 4]))
    os.write(fd, bytes([0x8f]))
    after = os.read(fd, 1)[0]
    assert after == before | 4, (before, after)
    print('THERMAL_ENABLE:' + json.dumps(dict(bus=bus, before=before, after=after)))
finally:
    os.close(fd)
PY
'''


def run(image, digest, output, *, replay=False):
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    fixture(image, output, digest)
    evidence = {'status': 'failed', 'base_sha256': digest, 'clean_shutdown': False,
                'replay': replay,
                'scope': 'disposable guest/controller thermal fault; no physical target'}
    owner = Controller({'test': output}, grants=('boot', 'force-stop', 'guest-exec', 'device-control'),
                       history=output / 'jobs.sqlite3', files_root=output)
    observer_directory = tempfile.TemporaryDirectory(prefix='uc-thermal-')
    observer_endpoint = Path(observer_directory.name) / 'observe.sock'
    original_command = uconsole_emulator.command

    def observed_command(*args, **kwargs):
        # A QEMU monitor accepts one client at a time. Give this test observer
        # its own private endpoint so it cannot occupy the controller's socket.
        return original_command(*args, **kwargs) + [
            '-qmp', f'unix:{observer_endpoint},server=on,wait=off']

    def job(name, **arguments):
        submitted = owner.call(name, {'workspace': 'test', **arguments})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            result = owner.job(submitted['job_id'])
            if result['status'] in ('completed', 'failed', 'cancelled'):
                return result
            time.sleep(0.05)
        raise TimeoutError('Controller job deadline: ' + submitted['job_id'])

    try:
        with patch('uconsole_emulator.command', side_effect=observed_command):
            evidence['boot'] = job('boot', mode='maintenance')
        evidence['observer'] = 'additional private QMP monitor; controller endpoint unchanged'
        if evidence['boot']['status'] != 'completed':
            raise ValueError('Guest boot failed')
        evidence['enable'] = job('guest_exec', script=ENABLE)
        enabled = evidence['enable']
        if (enabled['status'] != 'completed' or enabled['result']['exit_code'] or
                'THERMAL_ENABLE:' not in enabled['result']['stdout']):
            raise ValueError('Guest did not verify thermal shutdown enable')
        runtime = owner.runtimes['test']
        with socket.socket(socket.AF_UNIX) as monitor:
            monitor.settimeout(10)
            monitor.connect(str(observer_endpoint))
            with monitor.makefile('rwb', buffering=0) as events:
                if 'QMP' not in json.loads(events.readline()):
                    raise ValueError('Missing QMP greeting')
                events.write(b'{"execute":"qmp_capabilities","id":"observer"}\n')
                if json.loads(events.readline()).get('id') != 'observer':
                    raise ValueError('Observer capabilities not acknowledged')
                if replay:
                    schedule = output / 'fault-schedule.json'
                    schedule.write_text(json.dumps({'schema': 1, 'events': [
                        {'at_ms': 0, 'power': {'pmic_temperature_mc': 85000}},
                        {'at_ms': 10, 'power': {'pmic_over_temperature': True}},
                        {'at_ms': 20, 'power': {'pmic_temperature_mc': 25000}}]}) + '\n')
                    evidence['fault_job'] = job('power_replay', schedule_path=str(schedule))
                else:
                    evidence['fault_job'] = job('power_set', pmic_over_temperature=True)
                for _ in range(64):
                    event = json.loads(events.readline())
                    if event.get('event') == 'SHUTDOWN':
                        evidence['shutdown'] = event['data']
                        break
        if evidence.get('shutdown') != {'guest': False, 'reason': 'host-error'}:
            raise ValueError('Missing abrupt thermal shutdown event')
        evidence['exit_code'] = runtime.process.wait(timeout=10)
        rows = [json.loads(line) for line in Path(
            evidence['fault_job']['context']['evidence_path']).read_text().splitlines()]
        evidence['power_records'] = rows
        if sum(row.get('state') == 'dispatch' for row in rows) != (2 if replay else 1):
            raise ValueError('Unexpected recorded dispatch count')
        if replay:
            if any(row.get('event') == 2 for row in rows):
                raise ValueError('Replay attempted the event after thermal power loss')
            if not any(row.get('event') == 0 and row.get('status') == 'completed' for row in rows):
                raise ValueError('Pre-fault temperature event was not verified')
            if rows[-1].get('status') != 'failed' or rows[-1].get('rollback') is not False:
                raise ValueError('Replay did not preserve the partial failure')
        if evidence['fault_job']['status'] not in ('failed', 'completed'):
            raise ValueError('Fault job did not reach a supported terminal state')
        # A completed readback can race with exit; retain it without calling the
        # abrupt process exit clean. A failed readback never implies rollback.
        evidence['inspection'] = owner.inspect('test')
        if evidence['inspection']['runtime']['status'] != 'exited':
            raise ValueError('Owner did not recognize the dead guest')
        evidence['base_unchanged'] = sha256(image) == digest
        if not evidence['base_unchanged']:
            raise ValueError('Backing image changed')
        evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            owner.close()
        except BaseException as exc:
            evidence['status'] = 'failed'
            evidence['cleanup_error'] = str(exc)
            raise
        finally:
            observer_directory.cleanup()
            (output / 'thermal-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print('Thermal controller: ' + evidence['status'], flush=True)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay', action='store_true', help='Fault in event two; verify event three is not attempted')
    args = parser.parse_args()
    run(args.image, args.sha256, args.output, replay=args.replay)
