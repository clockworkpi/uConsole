"""Qualify synthetic USB capture with the stock Linux driver in a private VM."""
import argparse
import json
import inspect
from pathlib import Path
import shutil
import threading
import uuid

from forge_filesystem import check_overlay_root
from forge_runtime import Runtime
from forge_workspace import sha256
from uconsole_emulator import parser, wait_for_log
from validate_desktop_preparation_gui import fixture
from capture_pattern import verify_capture


PROBE = r'''import json, pathlib, subprocess, tempfile, time
deadline = time.monotonic() + 10
while True:
    cards = [p for p in pathlib.Path('/proc/asound').glob('card*/usbid') if p.read_text().strip() == '46f4:0003']
    if len(cards) == 1: break
    if time.monotonic() >= deadline: raise RuntimeError('Synthetic capture card not found')
    time.sleep(0.05)
card = cards[0].parent.name
number = int(card.removeprefix('card'))
driver = (pathlib.Path('/sys/class/sound') / card / 'device/driver').resolve().name
if driver != 'snd-usb-audio': raise RuntimeError('Unexpected driver: ' + driver)
with tempfile.TemporaryDirectory(prefix='forge-capture-') as temporary:
    raw = pathlib.Path(temporary) / 'capture.raw'
    recorded = subprocess.run(['arecord', '-D', 'hw:' + str(number) + ',0', '-t', 'raw',
        '-f', 'S16_LE', '-r', '48000', '-c', '2', '-d', '1', str(raw)],
        text=True, capture_output=True, timeout=20)
    if recorded.returncode: raise RuntimeError('Capture failed: ' + recorded.stderr)
    payload = raw.read_bytes()
    checked = verify_capture(payload)
    print(json.dumps({'status':'captured', 'driver':driver, 'usb_id':'46f4:0003',
        'frames':checked['frames'], 'rate':48000, 'channels':2, 'format':'S16_LE',
        'sequence':checked, 'stderr':recorded.stderr,
        'stream':(cards[0].parent / 'stream0').read_text()}))
'''


def run(image, digest, output, *, hotplug=False, gui=False, mcp=False):
    if (gui or mcp) and not hotplug:
        raise ValueError('Capture frontend qualification requires hotplug')
    if gui and mcp:
        raise ValueError('Select one capture frontend')
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError('Need 512 MiB free for capture qualification')
    fixture(image, output, digest)
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run', '--mode', 'maintenance',
                                         '--audio', 'usb-capture']))
    cancel = threading.Event()
    guest = '/tmp/forge-capture-' + uuid.uuid4().hex + '.py'
    evidence = {'status': 'failed', 'image_sha256': digest, 'forced_cleanup': False,
                'scope': 'synthetic USB capture; no host microphone or native audio'}
    frontend = None
    try:
        runtime.start()
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)
        setup = runtime.execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
            'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
            'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
            'modprobe snd_usb_audio; command -v arecord', cancel=cancel)
        evidence['setup'] = setup
        if setup['exit_code']:
            raise ValueError('Capture guest setup failed')
        source = output / 'capture-probe.py'
        source.write_text(inspect.getsource(verify_capture) + '\n' + PROBE)
        runtime.upload(source, guest)
        result = runtime.execute('python3 ' + guest, timeout=40, cancel=cancel)
        evidence['probe'] = result
        if result['exit_code']:
            raise ValueError('Guest capture failed; inspect retained probe evidence')
        evidence['capture'] = json.loads(result['stdout'])
        if evidence['capture'].get('status') != 'captured':
            raise ValueError('Guest did not confirm capture')
        if hotplug:
            from forge_audio import operation
            if gui:
                from audio_gui_validation import GUIAudio
                frontend = GUIAudio(runtime, output)
                operation = frontend.operation
            elif mcp:
                from audio_mcp_validation import MCPAudio
                frontend = MCPAudio(runtime, output)
                operation = frontend.operation
            evidence['disconnect'] = operation(runtime, output / 'capture-disconnect.jsonl', False)
            absent = runtime.execute("python3 - <<'PY'\n"
                "import pathlib,time\nend=time.monotonic()+10\n"
                "while any(p.read_text().strip()=='46f4:0003' for p in pathlib.Path('/proc/asound').glob('card*/usbid')):\n"
                " if time.monotonic()>end: raise RuntimeError('Capture card still present')\n"
                " time.sleep(0.05)\nprint('absent')\nPY", timeout=20, cancel=cancel)
            evidence['absent'] = absent
            if absent['exit_code'] or absent['stdout'].strip() != 'absent':
                raise ValueError('Guest capture removal was not confirmed')
            evidence['reconnect'] = operation(runtime, output / 'capture-reconnect.jsonl', True)
            replay = runtime.execute('python3 ' + guest, timeout=40, cancel=cancel)
            evidence['replay'] = replay
            if replay['exit_code'] or json.loads(replay['stdout']).get('status') != 'captured':
                raise ValueError('Capture did not recover after hotplug')
        cleanup = runtime.execute('rm -- ' + guest, cancel=cancel)
        if cleanup['exit_code']:
            raise ValueError('Guest capture cleanup failed')
        if frontend is not None:
            frontend.close()
            frontend = None
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Capture VM did not stop cleanly')
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        evidence['base_unchanged'] = sha256(image) == digest
        if not evidence['base_unchanged']:
            raise ValueError('Backing image changed')
        evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        try:
            if frontend is not None:
                try:
                    frontend.close()
                except Exception as exc:
                    evidence['frontend_cleanup_error'] = str(exc)
            if runtime.process is not None and runtime.process.poll() is None:
                try:
                    runtime.stop()
                except Exception as exc:
                    evidence['cleanup_error'] = str(exc)
                    evidence['forced_cleanup'] = True
                    runtime.stop(force=True)
        finally:
            (output / 'capture-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print('Capture guest: ' + evidence['status'] + '; ' + str(output), flush=True)
    return evidence


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--hotplug', action='store_true')
    cli.add_argument('--gui', action='store_true')
    cli.add_argument('--mcp', action='store_true')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, hotplug=args.hotplug, gui=args.gui, mcp=args.mcp)
