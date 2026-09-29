"""Disposable Linux ALSA qualification for the null-backed USB playback surrogate."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import threading
import uuid

from forge_filesystem import check_overlay_root
from forge_audio import operation as audio_operation
from forge_runtime import Runtime
from forge_workspace import sha256
from uconsole_emulator import parser, wait_for_log
from validate_desktop_preparation_gui import fixture
from audio_direct_probe import DIRECT_ALSA
from duplex_audio_probe import verified_evidence


PROBE = r'''import json, math, pathlib, re, struct, subprocess, sys, tempfile, wave
cards = [p for p in pathlib.Path('/proc/asound').glob('card*/usbid') if p.read_text().strip() == '46f4:0002']
if len(cards) != 1: raise RuntimeError('Expected exactly one QEMU USB audio card')
card = cards[0].parent.name
if not re.fullmatch('card[0-9]+', card): raise RuntimeError('Invalid ALSA card name')
number = int(card[4:])
driver = (pathlib.Path('/sys/class/sound') / card / 'device/driver').resolve().name
if driver != 'snd-usb-audio': raise RuntimeError('Production USB audio driver is not bound: ' + driver)
monitor = monitor_file = None
monitor_data = None
if '--usbmon' in sys.argv:
    subprocess.run(['/usr/sbin/modprobe', 'usbmon'], check=True)
    if subprocess.run(['/usr/bin/mountpoint', '-q', '/sys/kernel/debug']).returncode:
        subprocess.run(['/usr/bin/mount', '-t', 'debugfs', 'debugfs', '/sys/kernel/debug'], check=True)
    monitor_file = tempfile.TemporaryFile()
    monitor = subprocess.Popen(['/usr/bin/cat', '/sys/kernel/debug/usb/usbmon/0u'], stdout=monitor_file)
with tempfile.TemporaryDirectory(prefix='forge-audio-') as temporary:
    source = pathlib.Path(temporary) / 'tone.wav'
    with wave.open(str(source), 'wb') as stream:
        stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b''.join(struct.pack('<hh', value, value)
            for value in (int(4096 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000))))
    if '--direct-alsa' in sys.argv:
        result = play_direct(source, 'hw:' + str(number) + ',0', '--raw-drain' in sys.argv)
    else:
        result = subprocess.run(['aplay', '-D', 'hw:' + str(number) + ',0', str(source)],
                                text=True, capture_output=True, timeout=30)
    if monitor is not None:
        monitor.terminate()
        monitor.wait(timeout=5)
        monitor_file.seek(0)
        lines = [line for line in monitor_file.read().decode().splitlines() if ' Zo:' in line]
        monitor_file.close()
        monitor_data = {'first':lines[:4], 'last':lines[-4:], 'lines':len(lines)}
        for stage in ('S', 'C', 'E'):
            selected = [line for line in lines if line.split()[2] == stage]
            monitor_data[stage] = {'count':len(selected),
                'bytes':sum(int(re.search(r' (\d+) [=><](?: |$)', line).group(1)) for line in selected)}
        monitor_data['failed_completions'] = sum(line.split()[4].split(':')[0] != '0'
            for line in lines if line.split()[2] == 'C')
    if result.returncode: raise RuntimeError('ALSA playback failed: ' + result.stderr)
    absent = subprocess.run(['aplay', '-D', 'hw:999,0', str(source)], text=True, capture_output=True, timeout=10)
    if absent.returncode == 0: raise RuntimeError('Absent-card playback falsely succeeded')
print(json.dumps({'status':'played', 'card':card, 'usb_id':'46f4:0002', 'driver':driver,
    'usbmon':monitor_data,
    'playback':{'exit_code':result.returncode, 'stderr':result.stderr, 'frames':48000,
                'client_details':result.stdout,
                'rate':48000, 'channels':2, 'format':'S16_LE'},
    'absent_card_exit_code':absent.returncode,
    'capture_interfaces':[p.name for p in cards[0].parent.glob('pcm*c')],
    'stream':(cards[0].parent / 'stream0').read_text()}))
'''


def run(image, expected_sha256, output, *, hotplug=False, gui=False, mcp=False, wav=False, usbmon=False, direct_alsa=False, raw_drain=False, module=None, module_sha256=None, repetitions=1, duplex=False):
    if duplex and (not wav or usbmon or direct_alsa or raw_drain):
        raise ValueError('Duplex qualification requires WAV and the concurrent aplay/arecord probe')
    if type(repetitions) is not int or not 1 <= repetitions <= 10:
        raise ValueError('Repetitions must be an integer from 1 to 10')
    if bool(module) != bool(module_sha256):
        raise ValueError('Candidate module and explicit SHA-256 pin are required together')
    module_bytes = None
    if module is not None:
        module = Path(module).resolve()
        if module.stat().st_size > 8 * 1024**2:
            raise ValueError('Candidate audio module exceeds 8 MiB')
        module_bytes = module.read_bytes()
        if hashlib.sha256(module_bytes).hexdigest() != module_sha256:
            raise ValueError('Candidate audio module hash mismatch')
    if raw_drain and not direct_alsa:
        raise ValueError('Raw drain requires the direct ALSA diagnostic client')
    if (gui or mcp) and not hotplug:
        raise ValueError('Frontend audio qualification requires hotplug')
    if gui and mcp:
        raise ValueError('Select one audio frontend per qualification')
    image, output = Path(image).resolve(), Path(output).resolve()
    if sha256(image) != expected_sha256:
        raise ValueError('Backing image hash mismatch')
    if shutil.disk_usage(output.parent).free < 512 * 1024 * 1024:
        raise ValueError('Need at least 512 MiB free for disposable audio qualification')
    fixture(image, output, expected_sha256)
    runtime = Runtime(parser().parse_args(['--workspace', str(output), 'run', '--mode', 'maintenance',
                                         '--audio', 'usb-duplex' if duplex else ('usb-wav' if wav else 'usb-null')]))
    evidence = {'status': 'failed', 'image_sha256': expected_sha256, 'forced_cleanup': False,
                'scope': 'USB ALSA playback into WAV file' if wav else 'USB ALSA playback into null backend',
                'excluded': 'capture, host speakers, jack and native audio'}
    if duplex:
        evidence.update(scope='simultaneous USB WAV playback and synthetic capture',
                        excluded='host microphone/speakers, native jack and carrier audio')
    cancel = threading.Event()
    guest = '/tmp/forge-audio-probe-' + uuid.uuid4().hex + '.py'
    frontend = None
    try:
        runtime.start()
        wait_for_log(output / 'serial.log', b'root@(none):/#', runtime.process, 120)
        setup = runtime.execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                                'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                                'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
                                'modprobe snd_usb_audio; command -v aplay', cancel=cancel)
        evidence['setup'] = setup
        if setup['exit_code']:
            raise ValueError('Guest audio setup failed')
        module_guest = None
        if module_bytes is not None:
            frozen = output / 'candidate-snd-usb-audio.ko'
            frozen.write_bytes(module_bytes)
            module_guest = '/tmp/forge-audio-module-' + uuid.uuid4().hex + '.ko'
            runtime.upload(frozen, module_guest)
            loaded = runtime.execute('set -e; modprobe -r snd_usb_audio; '
                'modprobe -a mc snd_pcm snd_usbmidi_lib snd_hwdep; insmod ' + module_guest +
                '; uname -r', cancel=cancel)
            evidence['candidate_module'] = {'sha256': module_sha256, 'load': loaded}
            if loaded['exit_code']:
                raise ValueError('Candidate audio module did not load normally')
        source = output / 'audio-probe.py'
        if duplex:
            from duplex_audio_probe import source as duplex_source
            source.write_text(duplex_source())
        else:
            source.write_text(DIRECT_ALSA + '\n' + PROBE)
        runtime.upload(source, guest)
        probe_command = ('python3 ' + guest + (' --usbmon' if usbmon else '') +
                         (' --direct-alsa' if direct_alsa else '') + (' --raw-drain' if raw_drain else ''))
        result = runtime.execute(probe_command, timeout=60, cancel=cancel)
        evidence['probe'] = result
        if result['exit_code']:
            raise ValueError('Guest ALSA probe failed; inspect retained probe output')
        evidence['audio'] = json.loads(result['stdout'])
        if (evidence['audio'].get('status') != 'played' or
                (duplex and not verified_evidence(evidence['audio']))):
            raise ValueError('Guest did not acknowledge playback')
        evidence['repeated_playback'] = []
        for _ in range(repetitions - 1):
            repeated = runtime.execute(probe_command, timeout=60, cancel=cancel)
            evidence['repeated_playback'].append(repeated)
            if (repeated['exit_code'] or json.loads(repeated['stdout']).get('status') != 'played' or
                    (duplex and not verified_evidence(json.loads(repeated['stdout'])))):
                raise ValueError('Repeated ALSA playback failed')
        if hotplug:
            operation = audio_operation
            if gui:
                from audio_gui_validation import GUIAudio
                frontend = GUIAudio(runtime, output)
                operation = frontend.operation
            elif mcp:
                from audio_mcp_validation import MCPAudio
                frontend = MCPAudio(runtime, output)
                operation = frontend.operation
            def await_presence(present):
                required = ['46f4:0002', '46f4:0003'] if duplex else ['46f4:0002']
                script = ('import pathlib,time\nend=time.monotonic()+10\n'
                          'while True:\n'
                          ' found=[p.read_text().strip() for p in pathlib.Path("/proc/asound").glob("card*/usbid")]\n'
                          f' if all((identity in found) == {present!r} for identity in {required!r}): break\n'
                          ' if time.monotonic()>end: raise RuntimeError("USB audio presence deadline")\n'
                          ' time.sleep(0.05)\n'
                          'print("presence-verified")\n')
                observed = runtime.execute("python3 - <<'PY'\n" + script + 'PY', timeout=20, cancel=cancel)
                if observed['exit_code'] or observed['stdout'].strip() != 'presence-verified':
                    raise ValueError('Guest USB audio hotplug state not verified: ' + str(observed))
                return observed
            evidence['disconnect'] = operation(runtime, output / 'audio-disconnect.jsonl', False)
            evidence['hotplug_absent'] = await_presence(False)
            evidence['reconnect'] = operation(runtime, output / 'audio-reconnect.jsonl', True)
            evidence['hotplug_present'] = await_presence(True)
            replay = runtime.execute(probe_command, timeout=60, cancel=cancel)
            evidence['hotplug_probe'] = replay
            if (replay['exit_code'] or json.loads(replay['stdout']).get('status') != 'played' or
                    (duplex and not verified_evidence(json.loads(replay['stdout'])))):
                raise ValueError('ALSA playback did not recover after hotplug')
        cleanup = runtime.execute('rm -- ' + guest + (' ' + module_guest if module_guest else ''), cancel=cancel)
        if cleanup['exit_code']:
            raise ValueError('Guest probe cleanup failed')
        if frontend is not None:
            frontend.close()
            frontend = None
        runtime.stop()
        if runtime.process.returncode != 0:
            raise ValueError('Audio guest did not stop cleanly')
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        evidence['base_unchanged'] = sha256(image) == expected_sha256
        if not evidence['base_unchanged']:
            raise ValueError('Backing image changed')
        if wav:
            from verify_audio_recording import verify
            evidence['recording'] = verify(runtime.audio_recording, repetitions=repetitions + int(hotplug))
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
            (output / 'audio-acceptance.json').write_text(json.dumps(evidence, indent=2) + '\n')
            print('Audio guest: ' + evidence['status'] + '; ' + str(output / 'audio-acceptance.json'), flush=True)
    return evidence


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--image', required=True, type=Path)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('--hotplug', action='store_true')
    cli.add_argument('--gui', action='store_true', help='Exercise real Tk audio panel buttons (requires --hotplug)')
    cli.add_argument('--mcp', action='store_true', help='Exercise separate stdio MCP process (requires --hotplug)')
    cli.add_argument('--wav', action='store_true', help='Verify every emitted tone, including playback after hotplug')
    cli.add_argument('--duplex', action='store_true', help='With --wav, require simultaneous playback and exact synthetic capture')
    cli.add_argument('--usbmon', action='store_true', help='Retain compact guest isochronous transfer diagnostics')
    cli.add_argument('--direct-alsa', action='store_true', help='Compare direct libasound write/drain against aplay')
    cli.add_argument('--raw-drain', action='store_true', help='With --direct-alsa, bypass library drain via Linux PCM ioctl')
    cli.add_argument('--module', type=Path, help='Candidate snd-usb-audio module loaded only into disposable guest /tmp')
    cli.add_argument('--module-sha256', help='Required explicit candidate module hash; no force-loading')
    cli.add_argument('--repetitions', type=int, default=1, help='Initial playback/open/drain/close cycles (1..10); hotplug adds one replay')
    args = cli.parse_args()
    run(args.image, args.sha256, args.output, hotplug=args.hotplug, gui=args.gui, mcp=args.mcp, wav=args.wav, usbmon=args.usbmon, direct_alsa=args.direct_alsa, raw_drain=args.raw_drain, module=args.module, module_sha256=args.module_sha256, repetitions=args.repetitions, duplex=args.duplex)
