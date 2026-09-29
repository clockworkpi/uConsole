#!/usr/bin/env python3
"""Record a real Workbench/guest schematic walkthrough in a disposable overlay."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import tkinter as tk
import uuid
from unittest.mock import patch

from forge_workspace import sha256
from uconsole_workbench import Workbench
from validate_desktop_preparation_gui import fixture
from validate_keyboard_guest import verify_firmware_events
from workbench_schematic_trace import Playback


def run(image, digest, output, video=False):
    image, output = image.resolve(), output.resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    fixture(image, output, digest)
    evidence = dict(status='failed', base_sha256=digest, jobs=[],
                    scope='Real Linux guest and Tk Workbench; no physical hardware equivalence')
    root = tk.Tk()
    app = Workbench(root, output)
    errors = []
    original_error = app.report_error
    app.report_error = lambda action, exc: (errors.append(str(exc)), original_error(action, exc))
    recorder = None

    def wait(predicate, label, timeout=180):
        deadline = time.monotonic() + timeout
        failure = []
        def check():
            try:
                if errors:
                    raise RuntimeError('Workbench error: ' + errors[-1])
                if predicate():
                    root.quit()
                elif time.monotonic() > deadline:
                    raise TimeoutError(label)
                else:
                    root.after(20, check)
            except BaseException as exc:
                failure.append(exc)
                root.quit()
        root.after(0, check)
        root.mainloop()
        if failure:
            raise failure[0]

    def job(identifier, idle):
        wait(lambda: app.controller.job(identifier)['status'] in ('completed', 'failed', 'cancelled')
             and idle(), 'GUI job ' + identifier)
        result = app.controller.job(identifier)
        evidence['jobs'].append(result)
        if result['status'] != 'completed':
            raise ValueError('Job failed: ' + repr(result))
        return result['result']

    def execute(script):
        app.start_guest_command(script, 'Schematic walkthrough')
        result = job(app.guest_job, lambda: app.guest_job is None)
        if result['exit_code']:
            raise ValueError('Guest command failed: ' + repr(result))
        return result['stdout']

    def seen(component, state='active'):
        return any(event['component'] == component and event['state'] == state
                   for event in app.schematic.recording.data['events'])

    try:
        with patch('uconsole_workbench.history_default_path', return_value=output / 'jobs.sqlite3'):
            app.mode.set('maintenance')
            app.keyboard.set('composite')
            app.display.set('gtk')
            app.start()
        wait(lambda: not app.setup_panel.flow_active, 'Public Start completion')
        if not app.setup_panel.status.get().startswith('Emulator started'):
            raise ValueError(app.setup_panel.status.get())
        runtime = app.runtime
        evidence['runtime_identity'] = runtime.identity
        execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
                'modprobe usbhid; modprobe cdc_acm; modprobe evdev; '
                'modprobe i2c_bcm2835; modprobe axp20x_i2c; modprobe axp20x_ac_power; test -c /dev/fb0')
        app.show_schematic()
        panel = app.schematic
        root.geometry('900x980+0+0')
        panel.window.geometry('1000x980+900+0')
        wait(lambda: panel.observations.view('keyboard', time.monotonic())['state'] == 'present',
             'Live keyboard observation', 30)
        if video:
            recorder = subprocess.Popen(['ffmpeg', '-loglevel', 'warning', '-nostats', '-f', 'x11grab', '-video_size', '1900x1000',
                                         '-framerate', '12', '-i', os.environ['DISPLAY'], '-c:v', 'libx264',
                                         '-preset', 'ultrafast', '-threads', '2', '-pix_fmt', 'yuv420p', str(output / 'walkthrough.mp4')],
                                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=(output / 'video.log').open('wb'))
        panel.record()
        wait(lambda: len(panel.recording.data['events']) >= 7, 'Initial recorded observations', 30)
        panel.select('keyboard')
        assert app.filename is None and 'class KeyboardBridge' in app.editor.get('1.0', 'end')
        evidence['source_navigation'] = True
        panel.source.current(2)
        panel.open_source()
        assert 'static void uconsole_data' in app.editor.get('1.0', 'end')
        evidence['qemu_model_navigation'] = True
        probe_path = '/tmp/schematic-keyboard-probe.py'
        app.start_transfer('upload', Path(__file__).with_name('keyboard_guest_probe.py'), probe_path, 'Guest input probe')
        job(app.transfer, lambda: app.transfer is None)
        ready = 'UC_KEYBOARD_READY_' + uuid.uuid4().hex
        probe_output = '/tmp/schematic-keyboard-result.json'
        # This finite observer is intentionally a separate guest session. The
        # normal task runner correctly reaps background children in its group
        # before acknowledging completion; do not weaken that safety boundary.
        launcher = (
            'import subprocess; '
            f'out=open({probe_output!r},"wb"); '
            f'p=subprocess.Popen(["python3",{probe_path!r},"--ready",{ready!r},"--profile","firmware"],'
            'stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True); '
            'print(p.pid)')
        # Popen waits for successful exec after setsid; a shell "setsid -f"
        # can return before its child has escaped the task's cleanup group.
        evidence['guest_probe_pid'] = int(execute('python3 -c ' + shlex.quote(launcher)).strip())
        wait(lambda: ready in (output / 'serial.log').read_text(errors='replace'), 'Guest evdev ready', 30)
        app.keyboard_deck()
        deck = app.deck
        for contact in [('matrix', 4, 2), ('matrix', 4, 2), ('matrix', 7, 2),
                        ('matrix', 1, 0), ('matrix', 7, 2), ('matrix', 1, 0)]:
            previous = set(app.controller.jobs)
            deck.buttons[contact][0].invoke()
            submitted = set(app.controller.jobs) - previous
            if len(submitted) != 1:
                raise ValueError('Deck action did not submit one job: ' + deck.status.get())
            job(submitted.pop(), lambda: deck.job is None)
            if deck.failed:
                raise ValueError('Keyboard deck failed: ' + deck.status.get())
        observed = json.loads(execute('cat ' + probe_output))
        verify_firmware_events(observed)
        assert not observed['missing']
        evidence['guest_input'] = observed
        deck.close()
        wait(lambda: seen('keyboard'), 'Recorded HID delivery activity', 30)
        panel.select('power')
        assert execute('cat /sys/class/power_supply/axp22x-ac/online').strip() == '1'
        app.power_field.set('ac_present')
        app.power_value.set('false')
        submitted = app.live_power(change=True)
        changed = job(submitted['job_id'], lambda: app.power_job is None)
        assert changed['observed']['ac_present'] is False
        evidence['guest_power'] = execute('cat /sys/class/power_supply/axp22x-ac/online').strip()
        assert evidence['guest_power'] == '0'
        wait(lambda: seen('power'), 'Recorded power-state change', 30)
        # Deliberate guest framebuffer writes, not an invented heartbeat.
        script = "with open('/dev/fb0','r+b',buffering=0) as f: f.write(bytes([0,255,0,0])*4096)"
        runtime.control('screendump', {'filename': str(output / 'framebuffer-before.ppm')})
        execute('python3 -c ' + shlex.quote(script) + '; dd if=/dev/zero of=/var/tmp/schematic-io.bin bs=4096 count=256; sync')
        runtime.control('screendump', {'filename': str(output / 'framebuffer-after.ppm')})
        evidence['display_refresh_requested_for_evidence'] = True
        assert (output / 'framebuffer-before.ppm').read_bytes() != (output / 'framebuffer-after.ppm').read_bytes()
        wait(lambda: seen('display') and seen('storage'), 'Display and storage activity', 30)
        panel.select('display')
        sheet = panel.open_sheet()
        root.update()
        evidence['schematic_sheet'] = sheet.note.get()
        sheet.window.destroy()
        panel.stop_record()
        trace_path = output / 'observations.json'
        with patch('workbench_schematic_gui.filedialog.asksaveasfilename', return_value=str(trace_path)):
            panel.save_record()
        replay = Playback.load(trace_path)
        evidence['recorded_events'] = len(replay.data['events'])
        with patch('workbench_schematic_gui.filedialog.askopenfilename', return_value=str(trace_path)):
            panel.load_replay()
        wait(lambda: panel.view_time is not None and panel.view_time >= replay.duration,
             'Recorded playback complete', replay.duration + 10)
        assert panel.mode.get().startswith('REPLAY')
        evidence['replayed'] = True
        panel.live()
        app.poweroff()
        job(app.guest_job, lambda: app.guest_job is None)
        wait(lambda: app.process is None, 'Clean guest shutdown', 30)
        assert runtime.process.returncode == 0
        wait(lambda: panel.observations.identity is None, 'Stale identity reset', 5)
        evidence['stop_clears_identity'] = True
        evidence['base_unchanged'] = sha256(image) == digest
        assert evidence['base_unchanged']
        evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        panel = getattr(app, 'schematic', None)
        if panel is not None:
            evidence['last_observations'] = dict(panel.observations.values)
            if panel.recording is not None and panel.recording.data['events']:
                panel.stop_record()
                panel.recording.save(output / 'observations-incomplete.json')
        raise
    finally:
        try:
            if recorder is not None:
                try:
                    recorder.communicate(b'q\n', timeout=10)
                except subprocess.TimeoutExpired:
                    recorder.terminate()
                    try:
                        recorder.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        recorder.kill()
                        recorder.wait(timeout=3)
                evidence['video_exit_code'] = recorder.returncode
                if recorder.returncode != 0:
                    evidence.update(status='failed', video_error='Recorder did not exit cleanly')
            if app.runtime and app.runtime.process and app.runtime.process.poll() is None:
                app.release_serial()
                app.runtime.stop(force=True)
                evidence['forced_fixture_cleanup'] = True
            if app.controller:
                app.controller.close()
        finally:
            root.destroy()
            (output / 'schematic-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print(json.dumps({'status': evidence['status'], 'output': str(output)}), flush=True)
    if evidence['status'] != 'passed':
        raise RuntimeError('Schematic walkthrough failed; inspect acceptance evidence')


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--video', action='store_true')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, args.video)
