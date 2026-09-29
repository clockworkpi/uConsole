"""Boot and hotplug audio through the actual Workbench widgets, then test ALSA."""
import argparse
import json
import inspect
from pathlib import Path
import shutil
import threading
import time
from unittest.mock import patch
import uuid

from audio_gui_validation import GUIAudio
from forge_filesystem import check_overlay_root
from forge_workspace import sha256
from validate_audio_guest import PROBE
from validate_desktop_preparation_gui import fixture
from validate_capture_guest import PROBE as CAPTURE_PROBE
from capture_pattern import verify_capture


def button(parent, label):
    matches = []
    def visit(widget):
        if widget.winfo_class() == 'TButton' and widget.cget('text') == label:
            matches.append(widget)
        for child in widget.winfo_children():
            visit(child)
    visit(parent)
    if len(matches) != 1:
        raise ValueError('Expected exactly one Workbench button: ' + label)
    return matches[0]


def select_audio(parent, variable, value):
    matches = []
    def visit(widget):
        if widget.winfo_class() == 'TCombobox' and str(widget.cget('textvariable')) == str(variable):
            matches.append(widget)
        for child in widget.winfo_children():
            visit(child)
    visit(parent)
    if len(matches) != 1 or value not in matches[0].cget('values'):
        raise ValueError('Requested audio mode is missing from the Workbench selector')
    widget = matches[0]
    widget.current(widget.cget('values').index(value))
    widget.event_generate('<<ComboboxSelected>>')


def run(image, digest, output, *, capture=False):
    import tkinter as tk
    from uconsole_workbench import Workbench
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError('Insufficient space for disposable audio validation')
    fixture(image, output, digest)
    audio = 'usb-capture' if capture else 'usb-null'
    expected = 'captured' if capture else 'played'
    usb_id = '46f4:0003' if capture else '46f4:0002'
    initial_key = 'initial_capture' if capture else 'initial_playback'
    record = {'status': 'failed', 'forced_cleanup': False, 'audio': audio}
    root = tk.Tk()
    runtime = app = None
    try:
        with patch('uconsole_workbench.history_default_path', return_value=output / 'workbench-jobs.sqlite3'):
            app = Workbench(root, output)
            select_audio(root, app.audio, audio)
            app.mode.set('maintenance')
            button(root, 'Start').invoke()
        job_id = app.boot_job
        if not job_id:
            raise ValueError('Workbench Start did not submit boot')
        deadline = time.monotonic() + 150
        while app.boot_job:
            if time.monotonic() > deadline:
                raise TimeoutError('Workbench audio boot deadline')
            root.update()
            time.sleep(0.02)
        record['boot'] = app.controller.job(job_id)
        runtime = app.runtime
        if record['boot']['status'] != 'completed' or record['boot']['context']['audio'] != audio:
            raise ValueError('Workbench did not boot the requested audio device')
        button(root, 'Audio controls').invoke()
        if app.audio_panel is None:
            raise ValueError('Workbench did not open its audio panel')
        panel = GUIAudio.__new__(GUIAudio)
        panel.root, panel.controller, panel.panel = root, app.controller, app.audio_panel
        cancel = threading.Event()
        def execute(script, timeout=60):
            app.release_serial()
            result = runtime.execute(script, timeout=timeout, cancel=cancel)
            if result['exit_code']:
                raise ValueError('Audio guest command failed: ' + str(result))
            return result
        record['setup'] = execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
            'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
            'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; modprobe snd_usb_audio')
        source = output / 'audio-probe.py'
        source.write_text(inspect.getsource(verify_capture) + '\n' + CAPTURE_PROBE if capture else PROBE)
        guest = '/tmp/forge-audio-' + uuid.uuid4().hex + '.py'
        app.release_serial()
        runtime.upload(source, guest)
        record[initial_key] = execute('python3 ' + guest)
        for connected in (False, True):
            name = 'reconnect' if connected else 'disconnect'
            record[name] = panel.operation(runtime, None, connected)
            script = ('import pathlib,time\nend=time.monotonic()+10\nwhile True:\n'
                f' found=any(p.read_text().strip()=={usb_id!r} for p in pathlib.Path("/proc/asound").glob("card*/usbid"))\n'
                f' if found == {connected!r}: break\n'
                ' if time.monotonic()>end: raise RuntimeError("audio presence deadline")\n'
                ' time.sleep(0.05)\nprint("presence-verified")\n')
            record[name + '_linux'] = execute("python3 - <<'PY'\n" + script + 'PY')
        record['replayed'] = execute('python3 ' + guest)
        for key in (initial_key, 'replayed'):
            if json.loads(record[key]['stdout']).get('status') != expected:
                raise ValueError('Guest did not acknowledge requested audio operation')
        execute('rm -- ' + guest)
        app.release_serial()
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Guest shutdown was not clean')
        record['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        record['base_unchanged'] = sha256(image) == digest
        if not record['base_unchanged']:
            raise ValueError('Backing image changed')
        record['status'] = 'passed'
    except BaseException as exc:
        record['error'] = str(exc)
        raise
    finally:
        try:
            if app is not None:
                # Drain boot/device jobs before finding and shutting down the
                # exact owned VM; never adopt an unrelated QMP endpoint.
                if app.controller:
                    app.controller.executor.shutdown(wait=True)
                    runtime = app.controller.runtimes.get('gui', runtime)
                app.release_serial()
                if runtime and runtime.process and runtime.process.poll() is None:
                    try:
                        runtime.stop()
                    except Exception as exc:
                        record['cleanup_error'] = str(exc)
                        record['forced_cleanup'] = True
                        runtime.stop(force=True)
                app.release()
                if app.controller:
                    app.controller.close()
        except BaseException as exc:
            record['status'] = 'failed'
            record['cleanup_error'] = str(exc)
            raise
        finally:
            root.destroy()
            (output / 'workbench-audio-acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Workbench audio: ' + record['status'])
    return record


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', required=True, type=Path)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('--capture', action='store_true', help='Select and verify synthetic capture instead of null playback')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, capture=args.capture)
