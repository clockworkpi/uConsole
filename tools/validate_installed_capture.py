"""Boot a GUI-owned capture/duplex VM through extracted archive launchers.

Uses an isolated XDG directory and a prebuilt QEMU cache; this does not qualify
building QEMU from the installed package. Logs, jobs and workspace are retained.
"""
import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

from capture_pattern import verify_capture
from forge_filesystem import check_overlay_root
from forge_workspace import sha256
from target_mcp_validation import MCPTransitions
from uconsole_emulator import BUILD_ROOT
from uconsole_mcp import PROTOCOL
from validate_capture_guest import PROBE
from validate_desktop_preparation_gui import fixture
from duplex_audio_probe import source as duplex_source, verified_evidence
from verify_audio_recording import verify as verify_recording


def run(archive, image, digest, output, *, duplex=False, module=None, module_sha256=None,
        ignore_saved_state=False):
    if ignore_saved_state and sys.platform != 'darwin':
        raise ValueError('Ignoring AppKit saved state is only supported on macOS')
    if bool(module) != bool(module_sha256):
        raise ValueError('Candidate module and explicit SHA-256 pin are required together')
    if module is not None and not duplex:
        raise ValueError('Candidate module qualification requires duplex mode')
    module_bytes = None
    if module is not None:
        module = Path(module).resolve()
        if module.stat().st_size > 8 * 1024**2:
            raise ValueError('Candidate audio module exceeds 8 MiB')
        module_bytes = module.read_bytes()
        if hashlib.sha256(module_bytes).hexdigest() != module_sha256:
            raise ValueError('Candidate audio module hash mismatch')
    archive, image, output = (Path(p).resolve() for p in (archive, image, output))
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    if shutil.disk_usage(output.parent).free < 512 * 1024**2:
        raise ValueError('Need 512 MiB free for installed capture qualification')
    archive_digest = sha256(archive)
    build = BUILD_ROOT / 'emulator/qemu-build'
    qemu_digest = sha256(build / 'qemu-system-aarch64')
    fixture(image, output, digest)
    installed = output / 'installed'
    installed.mkdir(mode=0o700)
    audio = 'usb-duplex' if duplex else 'usb-capture'
    record = {'status': 'failed', 'archive_sha256': archive_digest, 'base_sha256': digest, 'audio': audio,
              'ignore_saved_state': ignore_saved_state,
              'scope': 'packaged launchers and GUI owner; prebuilt QEMU cache'}
    with tarfile.open(archive) as stream:
        stream.extractall(installed, filter='data')
    payload = installed / 'uconsole-workbench'
    data = output / 'user-data'
    cache = data / 'uconsole-workbench/emulator'
    cache.mkdir(parents=True, mode=0o700)
    (cache / 'qemu-build').symlink_to(build)
    record['qemu_sha256'] = qemu_digest
    files = output / 'files'
    files.mkdir(mode=0o700)
    source = files / 'capture-probe.py'
    source.write_text(duplex_source() if duplex else inspect.getsource(verify_capture) + '\n' + PROBE)
    guest = '/tmp/forge-installed-capture-' + uuid.uuid4().hex + '.py'
    env = dict(os.environ, XDG_DATA_HOME=str(data), XDG_STATE_HOME=str(output / 'user-state'))
    # No checkout imports or caller-selected resource roots may satisfy the
    # installed-package gate. The launchers set their own packaged roots.
    for name in ('PYTHONPATH', 'UCONSOLE_ROOT', 'UCONSOLE_BUILD_DIR'):
        env.pop(name, None)
    gui = None
    rpc = MCPTransitions(output, {})
    boot_requested = stopped = False

    def job(name, arguments, timeout=180):
        submitted = rpc.call(name, arguments)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = rpc.call('job_status', {'job_id': submitted['job_id']})
            if current['status'] in ('completed', 'failed', 'cancelled'):
                if current['status'] != 'completed':
                    raise ValueError(name + ' failed: ' + str(current))
                return current
            time.sleep(0.05)
        raise TimeoutError(name + ' deadline; retain owner and durable job state')

    sockets = Path(tempfile.mkdtemp(prefix='uc-installed-capture-'))
    with (output / 'installed-gui.log').open('x') as gui_log:
        endpoint = sockets / 'agent.sock'
        record['agent_socket'] = str(endpoint)
        try:
            argv = [str(payload / 'bin/uconsole-workbench'), '--workspace', str(output),
                    '--agent-socket', str(endpoint), '--agent-files-root', str(files)]
            if ignore_saved_state:
                argv += ['-ApplePersistenceIgnoreState', 'YES']
            for grant in ('boot', 'force-stop', 'guest-exec', 'transfer', 'device-control'):
                argv += ['--agent-allow', grant]
            gui = subprocess.Popen(argv, cwd=installed, env=env, stdout=gui_log, stderr=gui_log)
            record['gui_pid'] = gui.pid
            deadline = time.monotonic() + 30
            while not endpoint.exists():
                if gui.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Installed GUI attachment failed; inspect log')
                time.sleep(0.05)
            rpc.log = (output / 'mcp-stderr.log').open('x')
            rpc.transcript = (output / 'mcp-transcript.jsonl').open('x')
            rpc.process = subprocess.Popen([str(payload / 'bin/uconsole-mcp'), '--connect', str(endpoint)],
                cwd=installed, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=rpc.log, text=True, bufsize=1)
            initialized = rpc.request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                'clientInfo': {'name': 'installed-capture-acceptance', 'version': '1'}})
            if initialized['protocolVersion'] != PROTOCOL:
                raise ValueError('Unexpected packaged MCP protocol')
            rpc.request('notifications/initialized', notification=True)
            boot_requested = True
            record['boot'] = job('boot', {'workspace': 'gui', 'mode': 'maintenance', 'audio': audio})
            setup = 'set -e; mountpoint -q /proc || mount -t proc proc /proc; '
            setup += 'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
            setup += 'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; modprobe snd_usb_audio'
            record['setup'] = job('guest_exec', {'workspace': 'gui', 'script': setup})
            if record['setup']['result']['exit_code']:
                raise ValueError('Installed guest setup command failed')
            module_guest = None
            if module_bytes is not None:
                frozen = files / 'candidate-snd-usb-audio.ko'
                frozen.write_bytes(module_bytes)
                module_guest = '/tmp/forge-installed-audio-' + uuid.uuid4().hex + '.ko'
                record['module_upload'] = job('upload', {'workspace': 'gui',
                    'host_path': str(frozen), 'guest_path': module_guest})
                loaded = job('guest_exec', {'workspace': 'gui', 'script':
                    'set -e; modprobe -r snd_usb_audio; modprobe -a mc snd_pcm snd_usbmidi_lib snd_hwdep; '
                    'insmod ' + module_guest + '; uname -r'})
                record['candidate_module'] = {'sha256': module_sha256, 'load': loaded}
                if loaded['result']['exit_code']:
                    raise ValueError('Candidate module did not load normally')
            record['upload'] = job('upload', {'workspace': 'gui', 'host_path': str(source), 'guest_path': guest})
            def presence(present):
                ids = ['46f4:0002', '46f4:0003'] if duplex else ['46f4:0003']
                script = ('import pathlib,time\nend=time.monotonic()+10\nwhile True:\n'
                    ' found=[p.read_text().strip() for p in pathlib.Path("/proc/asound").glob("card*/usbid")]\n'
                    f' if all((identity in found) == {present!r} for identity in {ids!r}): break\n'
                    ' if time.monotonic()>end: raise RuntimeError("Audio presence deadline")\n'
                    ' time.sleep(0.05)\nprint("presence-verified")\n')
                result = job('guest_exec', {'workspace': 'gui',
                    'script': "python3 - <<'PY'\n" + script + 'PY', 'timeout': 20})
                if result['result']['exit_code'] or result['result']['stdout'].strip() != 'presence-verified':
                    raise ValueError('Installed guest did not confirm audio presence')
                return result
            for key in ('initial_duplex' if duplex else 'initial_capture', 'replayed'):
                if key == 'replayed':
                    record['disconnect'] = job('audio_set', {'workspace': 'gui', 'connected': False})
                    record['absent'] = presence(False)
                    record['reconnect'] = job('audio_set', {'workspace': 'gui', 'connected': True})
                    record['present'] = presence(True)
                observed = job('guest_exec', {'workspace': 'gui', 'script': 'python3 ' + guest, 'timeout': 40})
                record[key] = observed
                if observed['result']['exit_code']:
                    raise ValueError('Installed guest did not verify capture')
                payload = json.loads(observed['result']['stdout'])
                if (payload.get('status') != ('played' if duplex else 'captured') or
                        (duplex and not verified_evidence(payload))):
                    raise ValueError('Installed guest did not verify requested audio data')
            record['guest_cleanup'] = job('guest_exec', {'workspace': 'gui',
                'script': 'rm -- ' + guest + (' ' + module_guest if module_guest else '')})
            if record['guest_cleanup']['result']['exit_code']:
                raise ValueError('Installed guest probe cleanup failed')
            record['stop'] = job('stop', {'workspace': 'gui'})
            stopped = True
            record['root_after_stop'] = check_overlay_root(output, str(build / 'qemu-img'))
            record['base_unchanged'] = sha256(image) == digest
            if not record['base_unchanged']:
                raise ValueError('Backing image changed')
            if duplex:
                recording = Path(record['boot']['result']['audio_recording'])
                if (recording.is_symlink() or not recording.resolve().is_relative_to(output) or
                        recording.name != 'playback.wav' or recording.stat().st_mode & 0o777 != 0o600 or
                        recording.parent.stat().st_mode & 0o777 != 0o700):
                    raise ValueError('Packaged recording is not a private workspace artifact')
                record['recording'] = verify_recording(recording, repetitions=2)
            record['status'] = 'passed'
        except BaseException as exc:
            record['error'] = str(exc)
            raise
        finally:
            try:
                if boot_requested and not stopped and rpc.process is not None:
                    try:
                        record['cleanup_stop'] = job('stop', {'workspace': 'gui'})
                        stopped = True
                    except Exception as exc:
                        record['cleanup_error'] = str(exc)
                rpc.close()
            except BaseException as exc:
                record['status'] = 'failed'
                record['transport_cleanup_error'] = str(exc)
                raise
            finally:
                if gui is not None and gui.poll() is None and (stopped or not boot_requested):
                    gui.terminate()
                    gui.wait(timeout=10)
                record['owner_retained'] = gui is not None and gui.poll() is None
                if not record['owner_retained']:
                    endpoint.unlink(missing_ok=True)
                    sockets.rmdir()
                (output / 'installed-capture-acceptance.json').write_text(json.dumps(record, indent=2) + '\n')
                print('Installed capture: ' + record['status'], flush=True)
    return record


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--archive', type=Path, required=True)
    cli.add_argument('--image', type=Path, required=True)
    cli.add_argument('--sha256', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--duplex', action='store_true', help='Verify concurrent playback/capture and exact private WAV data')
    cli.add_argument('--module', type=Path, help='Optional candidate driver, loaded only into the disposable duplex guest')
    cli.add_argument('--module-sha256', help='Explicit candidate module SHA-256; no forced loading')
    cli.add_argument('--ignore-saved-state', action='store_true',
                     help='macOS only: skip AppKit window restoration for the tested GUI process')
    args = cli.parse_args()
    run(args.archive, args.image, args.sha256, args.output, duplex=args.duplex,
        module=args.module, module_sha256=args.module_sha256, ignore_saved_state=args.ignore_saved_state)
