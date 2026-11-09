#!/usr/bin/env python3
"""Real Tk-to-QEMU power-profile acceptance on a disposable workspace."""
import argparse
import json
from pathlib import Path
import time
import tempfile
import tkinter as tk
from unittest.mock import patch
import uuid

from uconsole_workbench import Workbench
from forge_replay import completed_event_records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    identity = uuid.uuid4().hex
    transcript = workspace / f'gui-client-{identity}.json'
    profile = Path(__file__).resolve().parents[1] / 'docs/scenarios/battery-discharge.json'
    root = tk.Tk()
    app = Workbench(root, workspace)
    evidence = {'validation': 'running', 'workspace': str(workspace)}
    failure = []
    state = 'boot'
    deadline = time.monotonic() + 150
    marker = 'UC_GUI_' + identity
    runtime = None
    fixtures = tempfile.TemporaryDirectory(prefix='uc-gui-replay-')

    def power_result(change=False):
        # Only this acceptance driver waits synchronously. The actual GUI
        # submits and returns immediately; its Tk poll renders the job result.
        submitted = app.live_power(change=change)
        value = app.controller.jobs[submitted['job_id']][2].result(timeout=20)
        app.poll_power()
        evidence.setdefault('power_jobs', []).append(app.controller.job(submitted['job_id']))
        return value

    def drive():
        nonlocal state, deadline, runtime
        try:
            if time.monotonic() > deadline:
                raise TimeoutError('GUI acceptance timed out in ' + state)
            output = app.console.get('1.0', 'end').replace('\r', '')
            if state == 'boot' and app.serial is not None and 'root@(none):/#' in output:
                runtime = app.runtime
                for operation in ('stop', 'cont'):
                    submitted = app.control(operation)
                    app.controller.jobs[submitted['job_id']][2].result(timeout=20)
                    app.poll_control()
                    evidence.setdefault('runstate_jobs', []).append(app.controller.job(submitted['job_id']))
                record = workspace / f'scenario-{runtime.identity}.json'
                evidence['scenario'] = json.loads(record.read_text())
                assert evidence['scenario']['source_sha256'] == app.initial_scenario.sha256
                script = (
                    'mount -t proc proc /proc; mount -t sysfs sysfs /sys; '
                    'modprobe i2c_bcm2835; modprobe axp20x_i2c; '
                    'modprobe axp20x_ac_power; modprobe axp20x_adc; modprobe axp20x_battery; '
                    'v=$(cat /sys/class/power_supply/axp22x-ac/online '
                    '/sys/class/power_supply/axp20x-battery/{present,voltage_now,current_now,capacity}) '
                    '&& printf "\\n%s%s\\n%s\\n" UC_GUI_ ' + identity + ' "$v"')
                app.entry.insert(0, script)
                app.send()
                state = 'readings'
            elif state == 'readings' and '\n' + marker + '\n' in output:
                readings = output.split('\n' + marker + '\n', 1)[1].splitlines()[:5]
                if len(readings) < 5:
                    root.after(100, drive)
                    return
                assert readings == ['0', '1', '3300000', '-250000', '25'], readings
                evidence['linux_readings'] = readings
                assert 'Verified initial power profile' in output
                assert power_result()['power']['ac_present'] is False
                app.power_field.set('ac_present')
                app.power_value.set('true')
                evidence['live_change'] = power_result(change=True)
                assert evidence['live_change']['observed']['ac_present'] is True
                app.entry.insert(0, 'v=$(cat /sys/class/power_supply/axp22x-ac/online) '
                                 '&& printf "\\n%s%s:%s\\n" UC_LIVE_ ' + identity + ' "$v"')
                app.send()
                state = 'live'
            elif state == 'live' and '\nUC_LIVE_' + identity + ':1\n' in output:
                evidence['linux_live_ac'] = 1
                schedule = profile.with_name('ac-cycle.json')
                with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(schedule)):
                    app.start_replay()
                state = 'replay'
            elif state == 'replay' and app.replay_future is None:
                assert app.replay_status.get() == 'Replay completed: 2 events', app.replay_status.get()
                events = [json.loads(line) for line in app.replay_evidence.read_text().splitlines()]
                verified = completed_event_records(events)
                assert [event['result']['observed']['ac_present'] for event in verified] == [False, True], events
                evidence['replay'] = events
                app.entry.insert(0, 'v=$(cat /sys/class/power_supply/axp22x-ac/online) '
                                 '&& printf "\\n%s%s:%s\\n" UC_REPLAY_ ' + identity + ' "$v"')
                app.send()
                state = 'replay-readback'
            elif state == 'replay-readback' and '\nUC_REPLAY_' + identity + ':1\n' in output:
                evidence['linux_after_replay_ac'] = 1
                schedule = Path(fixtures.name) / 'cancel.json'
                schedule.write_text(json.dumps({'schema': 1, 'events': [
                    {'at_ms': 0, 'power': {'ac_present': False}},
                    {'at_ms': 300000, 'power': {'ac_present': True}}]}))
                with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(schedule)):
                    app.start_replay()
                state = 'cancel-ready'
                deadline = time.monotonic() + 30
            elif state == 'cancel-ready':
                assert app.replay_future is not None, app.replay_status.get()
                if (app.replay_evidence.exists() and
                        '"event": 0, "status": "completed"' in app.replay_evidence.read_text()):
                    app.cancel_replay()
                    state = 'cancel-confirmed'
            elif state == 'cancel-confirmed' and app.replay_future is None:
                assert app.replay_status.get().startswith('Replay cancelled;'), app.replay_status.get()
                events = [json.loads(line) for line in app.replay_evidence.read_text().splitlines()]
                assert events[-1]['status'] == 'cancelled' and events[-1]['completed_events'] == 1, events
                assert [event['event'] for event in events if event.get('status') == 'dispatching'] == [0], events
                evidence['cancelled_replay'] = events
                assert power_result()['power']['ac_present'] is False
                app.entry.insert(0, 'v=$(cat /sys/class/power_supply/axp22x-ac/online) '
                                 '&& printf "\\n%s%s:%s\\n" UC_CANCEL_ ' + identity + ' "$v"')
                app.send()
                state = 'cancel-readback'
            elif state == 'cancel-readback' and '\nUC_CANCEL_' + identity + ':0\n' in output:
                evidence['linux_after_cancel_ac'] = 0
                app.poweroff()
                evidence['shutdown_job_id'] = app.guest_job
                state = 'shutdown'
                deadline = time.monotonic() + 60
            elif state == 'shutdown' and app.process is None:
                evidence['shutdown_job'] = app.controller.job(evidence['shutdown_job_id'])
                assert evidence['shutdown_job']['status'] == 'completed', evidence['shutdown_job']
                assert evidence['shutdown_job']['result'] == {'stopped': True}
                assert runtime.process.returncode == 0, runtime.process.returncode
                evidence['clean_shutdown'] = True
                evidence['validation'] = 'passed'
                root.quit()
                return
            elif app.process is None and app.boot_job is None:
                raise RuntimeError('Guest exited before acceptance completed')
            root.after(100, drive)
        except BaseException as exc:
            failure.append(exc)
            root.quit()

    try:
        # Only the operating-system file chooser is automated. Workbench loads
        # and validates the real file, launches the real runtime and uses its
        # own serial widgets, polling loop and shutdown path throughout.
        with patch('uconsole_workbench.filedialog.askopenfilename', return_value=str(profile)):
            app.load_scenario()
        root.update_idletasks()
        app.start()
        root.after(100, drive)
        root.mainloop()
        if failure:
            raise failure[0]
        assert evidence['validation'] == 'passed'
    except BaseException as exc:
        evidence['validation'] = 'failed'
        evidence['error'] = str(exc)
        raise
    finally:
        if app.replay_future is not None:
            app.cancel_replay()
            try:
                app.replay_future.result(timeout=30)
            except Exception:
                pass  # The durable job result is reported by poll_replay.
            app.poll_replay()
        runtime = runtime or app.runtime
        if app.controller:
            if app.boot_job:
                app.controller.cancel(app.boot_job)
            app.controller.close()
        if runtime and runtime.process is not None and runtime.process.poll() is None:
            evidence['forced_cleanup'] = True
            runtime.stop(force=True)
        evidence['console'] = app.console.get('1.0', 'end')
        app.release()
        root.destroy()
        fixtures.cleanup()
        with transcript.open('x') as output:
            json.dump(evidence, output, indent=2)
            output.write('\n')
        print(f'GUI acceptance: {evidence["validation"]}; {transcript}', flush=True)


if __name__ == '__main__':
    main()
