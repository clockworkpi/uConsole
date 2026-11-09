#!/usr/bin/env python3
"""Drive real Tk ADC controls and stock guest IIO in a disposable overlay."""
import argparse
import json
from pathlib import Path
import shlex
import time
import tkinter as tk
from unittest.mock import patch

from forge_filesystem import check_overlay_root
from forge_workspace import sha256
from uconsole_emulator import executable
from uconsole_workbench import Workbench
from validate_adc101c_guest import READER
from validate_desktop_preparation_gui import fixture


def run(image, digest, output, *, reference='fixed'):
    if reference not in ('fixed', 'missing'):
        raise ValueError('Unknown reference profile')
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    fixture(image, output, digest)
    evidence = {'status': 'failed', 'base_sha256': digest, 'jobs': [], 'samples': [],
                'reference_profile': reference,
                'scope': 'real Workbench controls and guest IIO, not physical equivalence'}
    root = tk.Tk()
    app = Workbench(root, output)
    runtime = None

    def wait(predicate, description, timeout=180):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(description)
            root.update()
            time.sleep(0.01)

    def job(identifier, idle):
        wait(lambda: app.controller.job(identifier)['status'] in
             ('completed', 'failed', 'cancelled') and idle(), 'GUI job: ' + identifier)
        result = app.controller.job(identifier)
        evidence['jobs'].append(result)
        if result['status'] != 'completed':
            raise ValueError('GUI job failed: ' + repr(result))
        return result['result']

    def execute(script):
        app.start_guest_command(script, 'ADC acceptance')
        result = job(app.guest_job, lambda: app.guest_job is None)
        if result['exit_code']:
            raise ValueError('Guest command failed: ' + repr(result))
        return result['stdout']

    def sample(expected):
        reply = execute('python3 -c ' + shlex.quote(READER))
        observed = json.loads(next(line.split(':', 1)[1] for line in reply.splitlines()
                                   if line.startswith('ADC_SAMPLE:')))
        evidence['samples'].append(observed)
        assert observed['reference_supply_present'] == (reference == 'fixed'), observed
        if expected is None:
            assert observed['readings'] == [{'errno': 5}] * 3, observed
        else:
            assert observed['readings'][-1] == expected, observed
            if reference == 'missing':
                assert observed['scale'] == {'errno': 22}, observed
            else:
                assert abs(float(observed['scale']) - 3300 / 1024) < 1e-8, observed

    try:
        # Isolate only the history location. No runtime/control/guest mocks.
        with patch('uconsole_workbench.history_default_path', return_value=output / 'jobs.sqlite3'):
            app.mode.set('maintenance')
            app.adc_reference.set(reference)
            app.start()
        job(app.boot_job, lambda: app.boot_job is None)
        assert evidence['jobs'][0]['context']['adc_reference'] == reference
        runtime = app.runtime
        execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                'modprobe i2c_bcm2835; modprobe ti_adc081c')
        for field, value, expected in (
                ('adc_input_uv', 0, 0), ('adc_input_uv', 1650000, 512),
                ('adc_input_uv', 3300000, 1023), ('adc_powered', False, None)):
            app.power_field.set(field)
            app.power_value.set(json.dumps(value))
            submitted = app.live_power(change=True)
            changed = job(submitted['job_id'], lambda: app.power_job is None)
            assert changed['observed'][field] == value, changed
            sample(expected)
        schedule = output / 'adc-recovery.json'
        schedule.write_text(json.dumps({'schema': 1, 'events': [
            {'at_ms': 0, 'power': {'adc_input_uv': 1650000}},
            {'at_ms': 10, 'power': {'adc_powered': True}}]}) + '\n')
        # Automate only the native chooser; parsing and replay are real.
        with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(schedule)):
            app.start_replay()
        replay = job(app.replay_job, lambda: app.replay_job is None)
        assert replay['completed_events'] == 2, replay
        assert app.replay_status.get() == 'Replay completed: 2 events'
        sample(512)
        app.poweroff()
        job(app.guest_job, lambda: app.guest_job is None)
        wait(lambda: app.process is None, 'GUI release after shutdown', 30)
        assert runtime.process.returncode == 0
        evidence['root_after_stop'] = check_overlay_root(output, executable('qemu-img'))
        evidence['base_unchanged'] = sha256(image) == digest
        assert evidence['base_unchanged']
        evidence['status'] = 'passed'
    except BaseException as error:
        evidence['error'] = str(error)
        raise
    finally:
        evidence['console'] = app.console.get('1.0', 'end')
        runtime = runtime or app.runtime
        if app.controller:
            app.controller.close()
        if runtime and runtime.process is not None and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            runtime.stop(force=True)
        app.release()
        root.destroy()
        (output / 'adc-gui-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('ADC GUI: ' + evidence['status'], flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--reference', choices=['fixed', 'missing'], default='fixed')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, reference=args.reference)


if __name__ == '__main__':
    main()
