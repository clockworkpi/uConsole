#!/usr/bin/env python3
"""CM4 development environment using QEMU's partial BCM2711 model."""
import argparse
import base64
import bz2
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid

from emulator_image import BootPartition
from emulator_dtb import patch_strings
from forge_workspace import Workspace, WorkspaceLock, sync_directory, sync_file
from forge_boot import CM4_PATCH, refresh_boot
from forge_audio import MODES as AUDIO_MODES, RECORDING_MODES

SOURCE_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get('UCONSOLE_ROOT', SOURCE_ROOT)).resolve()
BUILD_ROOT = Path(os.environ.get('UCONSOLE_BUILD_DIR', ROOT / 'build')).expanduser().resolve()
DEFAULT = BUILD_ROOT / 'emulator/workspace'
IMAGE_SHA256 = 'ef95242cdb0125e8ed08157400a26d4665acddd083481ec2314acd6e073b74ab'
SURROGATE_WIDTH = 1280
SURROGATE_HEIGHT = 720
SURROGATE_DEPTH = 32
SURROGATE_SCHEMA = 16
DISPLAY_BACKENDS = ('none', 'gtk', 'sdl', 'cocoa')


def native_display():
    """Preferred local QEMU window, not a claim that a custom build includes it."""
    return 'cocoa' if sys.platform == 'darwin' else 'gtk'


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def executable(name):
    from workbench_emulator import selected
    managed = selected(ROOT, BUILD_ROOT)
    if managed is not None and name in ('qemu-system-aarch64', 'qemu-img'):
        return str(managed / name)
    suffix = '.exe' if os.name == 'nt' else ''
    local = BUILD_ROOT / 'emulator/qemu-build' / (name + suffix)
    return str(local) if local.is_file() else (shutil.which(name) or name)


def read_config(workspace):
    return json.loads((workspace / 'machine.json').read_text())


def require_private_image_host():
    # chmod's Windows read-only bit is not a private-file ACL. Do not silently
    # weaken image confidentiality or create a partial file before refusing.
    if not callable(getattr(os, 'fchmod', None)):
        raise RuntimeError('Private image import/export requires POSIX file permissions (Linux/macOS)')


def prepare(args):
    require_private_image_host()
    source = args.image.resolve()
    workspace = args.workspace.resolve()
    if workspace.exists():
        raise ValueError('Workspace already exists; choose a new directory')
    actual = digest(source)
    expected = args.sha256 or (IMAGE_SHA256 if source.suffix == '.bz2' else None)
    if not expected:
        raise ValueError('For a raw/custom image, supply --sha256 with its expected hash')
    if actual.lower() != expected.lower():
        raise ValueError(f'Image checksum mismatch: {actual}')
    workspace.mkdir(parents=True, mode=0o700)
    base = workspace / 'base.img'
    try:
        opener = bz2.open if source.suffix == '.bz2' else open
        with opener(source, 'rb') as inp, base.open('xb') as out:
            os.fchmod(out.fileno(), 0o600)
            while chunk := inp.read(1024 * 1024):
                if chunk == b'\0' * len(chunk):
                    out.seek(len(chunk), 1)
                else:
                    out.write(chunk)
            out.truncate()
        with BootPartition(base) as boot:
            boot.extract('kernel8.img', workspace / 'kernel8.img')
            boot.extract('bcm2711-rpi-cm4.dtb', workspace / 'cm4-original.dtb')
        # Firmware config.txt is not executed by QEMU's direct kernel loader.
        # Activate the modeled DWC2 controller; do not apply the unmodeled panel/PMIC.
        dtb = patch_strings((workspace / 'cm4-original.dtb').read_bytes(), CM4_PATCH)
        (workspace / 'cm4-qemu.dtb').write_bytes(dtb)
        with base.open('rb') as stream:
            stream.seek(440)
            disk_id, = struct.unpack('<I', stream.read(4))
        # QEMU SD cards require a power-of-two size. Only the overlay grows.
        size = 1 << (base.stat().st_size - 1).bit_length()
        subprocess.run([args.qemu_img, 'create', '-f', 'qcow2', '-F', 'raw',
                        '-b', str(base), str(workspace / 'disk.qcow2'), str(size)], check=True)
        config = {'schema': 1, 'machine': 'raspi4b', 'coverage': 'partial-cm4',
                  'source': str(source), 'source_sha256': actual,
                  'base_sha256': digest(base), 'base_bytes': base.stat().st_size,
                  'root': f'PARTUUID={disk_id:08x}-02',
                  'kernel_sha256': digest(workspace / 'kernel8.img'),
                  'dtb_sha256': digest(workspace / 'cm4-qemu.dtb')}
        (workspace / 'machine.json').write_text(json.dumps(config, indent=2) + '\n')
    except Exception:
        # Retain diagnostic files; never treat an incomplete preparation as usable.
        print(f'Preparation failed; partial files retained in {workspace}', file=sys.stderr)
        raise
    print(f'Prepared {workspace}\nCoverage: partial CM4; see docs/emulator.md')


def command(args, control_dir=None, serial_log=None, audio_output=None):
    workspace = args.workspace.resolve()
    config = read_config(workspace)
    keyboard = getattr(args, 'keyboard', 'generic')
    audio = getattr(args, 'audio', 'none')
    modem = getattr(args, 'modem', 'none')
    if modem not in ('none', 'composite'):
        raise ValueError('Unsupported modem model')
    if modem == 'composite' and (control_dir is None or args.modem_at_port):
        raise ValueError('Composite modem requires an owned runtime without --modem-at-port')
    if audio not in AUDIO_MODES:
        raise ValueError('Unsupported audio surrogate')
    if audio in RECORDING_MODES and audio_output is None:
        raise ValueError(audio + ' requires an owned runtime recording path; use --managed')
    if keyboard not in ('generic', 'composite'):
        raise ValueError('Unsupported keyboard model')
    if keyboard == 'composite' and args.keyboard_cdc_port:
        raise ValueError('Composite keyboard cannot use the split --keyboard-cdc-port surrogate')
    ports = [args.qmp_port, args.serial_port, args.gdb_port, args.ssh_port,
             args.keyboard_cdc_port, args.modem_at_port]
    if args.vnc_display is not None:
        ports.append(5900 + args.vnc_display)
        if args.display != 'none':
            raise ValueError('--vnc-display cannot be combined with a local display backend')
    ports = [port for port in ports if port is not None]
    if any(not 1 <= port <= 65535 for port in ports) or len(ports) != len(set(ports)):
        raise ValueError('Listener ports must be distinct and between 1 and 65535')
    # systemd-fsck-root skips a root already mounted writable. Normal boots
    # must permit the guest's early check/remount sequence after an interruption.
    root_access = 'rw' if args.mode == 'maintenance' else 'ro'
    cmdline = (f'earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1,115200 '
               f'root={config["root"]} rootfstype=ext4 rootwait {root_access} '
               f'bcm2708_fb.fbwidth={SURROGATE_WIDTH} '
               f'bcm2708_fb.fbheight={SURROGATE_HEIGHT} '
               f'bcm2708_fb.fbdepth={SURROGATE_DEPTH}')
    if args.mode == 'maintenance':
        cmdline += ' init=/bin/bash'
    elif args.mode == 'desktop':
        # Physical-board services otherwise wait indefinitely for modem GPIOs
        # or fail against the intentionally absent CM4 EEPROM device.
        cmdline += (' uconsole.emulator=1 systemd.unit=graphical.target'
                    ' systemd.wants=uconsole-emulator-desktop.service'
                    ' systemd.mask=uconsole-4g-cm4.service'
                    ' systemd.mask=rpi-eeprom-update.service'
                    ' systemd.mask=dev-dri-card0.device'
                    ' systemd.mask=dev-dri-renderD128.device'
                    ' systemd.mask=wayvnc.service'
                    ' systemd.mask=plymouth-start.service'
                    ' systemd.mask=plymouth-read-write.service'
                    ' systemd.mask=plymouth-quit.service'
                    ' systemd.mask=plymouth-quit-wait.service')
    else:
        cmdline += ' systemd.unit=multi-user.target'
    # raspi4b creates its SD card from the legacy IF_SD drive. Escape commas in
    # QEMU key/value syntax; subprocess receives paths without shell parsing.
    drive = str(workspace / 'disk.qcow2').replace(',', ',,')
    qmp_address = (f'unix:{control_dir}/qmp,server=on,wait=off' if control_dir else
                   f'tcp:127.0.0.1:{args.qmp_port},server=on,wait=off')
    cmd = [args.qemu, '-machine', 'raspi4b', '-accel', 'tcg',
           '-kernel', str(workspace / 'kernel8.img'),
           '-dtb', str(workspace / 'cm4-qemu.dtb'),
           '-drive', f'if=sd,format=qcow2,file={drive}',
           '-append', cmdline, '-display', args.display, '-monitor', 'none',
           '-qmp', qmp_address]
    adc_reference = getattr(args, 'adc_reference', 'fixed')
    if adc_reference not in ('fixed', 'missing'):
        raise ValueError('Unknown ADC reference profile')
    if adc_reference == 'missing':
        cmd += ['-global', 'adc101c.reference-supply-present=false']
    if keyboard == 'composite':
        cmd += ['-device', 'usb-uconsole-keyboard,id=deck']
    else:
        cmd += ['-device', 'usb-kbd', '-device', 'usb-mouse']
    if audio in ('usb-null', 'usb-wav', 'usb-duplex'):
        backend = ('none,id=forge-audio' if audio == 'usb-null' else
                   'wav,id=forge-audio,path=' + str(audio_output).replace(',', ',,') +
                   ',out.frequency=48000,out.channels=2,out.format=s16')
        cmd += ['-audiodev', backend,
                '-device', 'usb-audio,id=audio-surrogate,audiodev=forge-audio']
    if audio == 'usb-capture':
        cmd += ['-device', 'usb-forge-capture,id=audio-surrogate']
    if audio == 'usb-duplex':
        cmd += ['-device', 'usb-forge-capture,id=audio-capture']
    if args.serial_port or control_dir:
        # Path-valued chardev options use QEMU's comma escaping.
        logfile = str(serial_log or workspace / 'serial.log').replace(',', ',,')
        address = (f'path={control_dir}/serial' if control_dir else
                   f'host=127.0.0.1,port={args.serial_port}')
        cmd += ['-chardev', f'socket,id=uart,{address},server=on,wait=off,logfile={logfile}',
                '-serial', 'chardev:uart']
    else:
        cmd += ['-serial', 'stdio']
    if args.gdb_port:
        cmd += ['-gdb', f'tcp:127.0.0.1:{args.gdb_port}']
    if args.pause or getattr(args, 'scenario', None):
        cmd += ['-S']
    if args.vnc_display is not None:
        cmd += ['-vnc', f'127.0.0.1:{args.vnc_display}']
    if args.ssh_port:
        cmd += ['-netdev', f'user,id=net,hostfwd=tcp:127.0.0.1:{args.ssh_port}-:22',
                '-device', 'usb-net,netdev=net']
    if args.keyboard_cdc_port:
        cmd += ['-chardev', f'socket,id=keyboard-cdc,host=127.0.0.1,port={args.keyboard_cdc_port},server=on,wait=off',
                '-device', 'usb-serial,chardev=keyboard-cdc,serial=20230713']
    if args.modem_at_port:
        cmd += ['-chardev', f'socket,id=modem-at,host=127.0.0.1,port={args.modem_at_port},server=on,wait=off',
                '-device', 'usb-serial,chardev=modem-at,serial=uconsole-modem-at']
    if modem == 'composite':
        for name in ('primary', 'secondary'):
            path = str(control_dir / ('modem-' + name)).replace(',', ',,')
            cmd += ['-chardev', f'socket,id=modem-{name},path={path},server=on,wait=off']
        cmd += ['-netdev', 'user,id=modem-network,net=10.0.3.0/24',
                '-device', 'usb-forge-modem,id=modem-device,primary=modem-primary,'
                'secondary=modem-secondary,netdev=modem-network']
    return cmd


def run(args):
    if args.audio in RECORDING_MODES and not args.managed:
        raise ValueError(args.audio + ' requires --managed to allocate a private recording')
    if args.scenario and not args.managed:
        raise ValueError('--scenario requires --managed owned runtime controls')
    if args.dry_run:
        preview = args.workspace / '<allocated-at-launch>' / 'playback.wav' if args.audio in RECORDING_MODES else None
        print(json.dumps(command(args, audio_output=preview), indent=2))
        return
    models = subprocess.check_output([args.qemu, '-machine', 'help'], text=True)
    if 'raspi4b ' not in models:
        raise ValueError('QEMU lacks raspi4b; run tools/build_emulator_qemu.py')
    if args.managed:
        from forge_runtime import Runtime
        runtime = Runtime(args).start()
        print(json.dumps({'identity': runtime.identity,
                          'qmp_socket': str(runtime.qmp_endpoint),
                          'serial_socket': str(runtime.serial_endpoint)}), flush=True)
        try:
            return runtime.process.wait()
        except KeyboardInterrupt:
            if runtime.process.poll() is None:
                runtime.stop(force=True)
            return 130
        finally:
            runtime.release()
    with WorkspaceLock(args.workspace) as lock:
        refresh_boot(args.workspace, args.qemu_img)
        cmd = command(args)
        (args.workspace / 'last-command.json').write_text(json.dumps(cmd, indent=2) + '\n')
        return subprocess.call(cmd, pass_fds=(lock.fileno(),))


def desktop_setup_files():
    """Return overlay-local configuration for the Xorg/fbdev surrogate."""
    xorg = '''Section "Device"
    Identifier "uConsole surrogate framebuffer"
    Driver "fbdev"
    Option "fbdev" "/dev/fb0"
EndSection
Section "Screen"
    Identifier "uConsole surrogate screen"
    Device "uConsole surrogate framebuffer"
    DefaultDepth 24
EndSection
'''
    launcher = '''#!/usr/bin/python3
import configparser
import os
from pathlib import Path
import sys

binary = '/usr/sbin/lightdm'
if 'uconsole.emulator=1' not in Path('/proc/cmdline').read_text().split():
    os.execv(binary, [binary, *sys.argv[1:]])

os.umask(0o077)
config = configparser.ConfigParser(interpolation=None, strict=False)
config.optionxform = str
config.read(Path('/etc/lightdm/lightdm.conf'))
if not config.has_section('LightDM'):
    config.add_section('LightDM')
# The mailbox framebuffer has no DRM device advertising a graphical seat.
config.set('LightDM', 'logind-check-graphical', 'false')
if not config.has_section('Seat:*'):
    config.add_section('Seat:*')
config.set('Seat:*', 'xserver-command', '/usr/bin/Xorg -config /etc/uconsole-emulator/xorg.conf -nolisten tcp')
config.set('Seat:*', 'user-session', 'rpd-x')
config.set('Seat:*', 'autologin-session', 'rpd-x')
directory = Path('/run/uconsole-emulator')
directory.mkdir(mode=0o755, exist_ok=True)
target = directory / 'lightdm.conf'
with target.open('w') as stream:
    config.write(stream)
os.execv(binary, [binary, '--config', str(target), *sys.argv[1:]])
'''
    session = '''#!/usr/bin/python3
import configparser
import os
from pathlib import Path
import shutil
import tempfile

if ('uconsole.emulator=1' in Path('/proc/cmdline').read_text().split() and
        os.environ.get('DESKTOP_SESSION') == 'rpd-x'):
    original = os.environ.get('XDG_CONFIG_DIRS') or '/etc/xdg'
    dirs = [Path(p) for p in original.split(':') if p.startswith('/')]
    personal = os.environ.get('XDG_CONFIG_HOME', '')
    personal = Path(personal) if personal.startswith('/') else Path.home() / '.config'
    config = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        config.read([p / 'lxsession/rpd-x/desktop.conf' for p in reversed(dirs)] +
                    [personal / 'lxsession/rpd-x/desktop.conf'])
        agent = config.get('Session', 'polkit/command', fallback='').strip()
    except configparser.Error:
        agent = ''
    # Retain the RPD X session's chosen agent. Do not alter user autostart
    # files, other desktops, or the native boot environment.
    if agent in ('lxpolkit', '/usr/bin/lxpolkit') and shutil.which('lxpolkit'):
        runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or '/run/user/' + str(os.getuid()))
        info = runtime.stat() if runtime.is_dir() else None
        if info and info.st_uid == os.getuid() and not info.st_mode & 0o077:
            profile = runtime / os.path.basename(tempfile.mkdtemp(prefix='uconsole-xdg-', dir=runtime))
            (profile / 'autostart').mkdir()
            mate = 'polkit-mate-authentication-agent-1.desktop'
            (profile / 'autostart' / mate).symlink_to('/etc/uconsole-emulator/xdg/autostart/' + mate)
            # Some RPD programs consult only the first system config directory.
            # Forward their normal entries as well as overriding one autostart.
            for directory in dirs:
                for origin, target in [(directory, profile), (directory / 'autostart', profile / 'autostart')]:
                    if not origin.is_dir():
                        continue
                    for item in origin.iterdir():
                        link = target / item.name
                        if not link.exists() and not link.is_symlink():
                            link.symlink_to(item)
            print(':'.join([str(profile)] + [str(p) for p in dirs]))
'''
    unit = '''[Unit]
Description=uConsole emulator surrogate desktop
ConditionKernelCommandLine=uconsole.emulator=1
After=lightdm.service
Requires=lightdm.service

[Service]
Type=oneshot
ExecStart=/bin/true
RemainAfterExit=yes
'''
    return {
        '/etc/uconsole-emulator/xorg.conf': xorg,
        '/usr/local/libexec/uconsole-emulator-desktop': launcher,
        '/usr/local/libexec/uconsole-emulator-session': session,
        '/etc/X11/Xsession.d/90uconsole-emulator':
            '# Sourced by Xsession; no changes outside an emulator boot.\n'
            'case " $(cat /proc/cmdline) " in\n'
            '  *" uconsole.emulator=1 "*)\n'
            '    UCONSOLE_SESSION_CONFIG_DIRS=$(/usr/local/libexec/uconsole-emulator-session || true)\n'
            '    if [ -n "$UCONSOLE_SESSION_CONFIG_DIRS" ]; then\n'
            '      export XDG_CONFIG_DIRS="$UCONSOLE_SESSION_CONFIG_DIRS"\n'
            '    fi\n'
            '    unset UCONSOLE_SESSION_CONFIG_DIRS\n'
            '    ;;\n'
            'esac\n',
        '/etc/uconsole-emulator/xdg/autostart/polkit-mate-authentication-agent-1.desktop':
            '[Desktop Entry]\nType=Application\nName=MATE PolicyKit agent\nHidden=true\n',
        '/etc/systemd/system/uconsole-emulator-desktop.service': unit,
        # Keep is-enabled truthful: the image's first-boot wizard uses it to
        # select graphical vs console configuration for the deployed image.
        '/etc/systemd/system/lightdm.service.d/90-uconsole-emulator.conf':
            '[Service]\nExecStart=\nExecStart=/usr/local/libexec/uconsole-emulator-desktop\n',
        '/etc/systemd/system/getty@tty1.service.d/90-uconsole-emulator.conf':
            '[Unit]\nConditionKernelCommandLine=!uconsole.emulator=1\n',
    }


def desktop_setup_commands():
    """Install opt-in adapters without changing native display defaults.

    Early experimental setups destroyed the original display-manager selection.
    Without a backup we cannot infer it safely; require explicit recovery before
    touching those images. Each transaction fits a canonical serial input line.
    """
    commands = [
        'if [ -e /etc/X11/xorg.conf.d/20-uconsole-emulator-fbdev.conf ] || '
        '[ "$(readlink /etc/systemd/system/display-manager.service)" = '
        '"/etc/systemd/system/uconsole-emulator-desktop.service" ]; then '
        'echo "Legacy emulator display defaults require recovery from the original '
        'image before setup; user configuration will not be guessed" >&2; exit 1; fi',
    ]
    for destination, content in desktop_setup_files().items():
        encoded = base64.b64encode(content.encode()).decode()
        commands += [f'install -d {shlex.quote(str(Path(destination).parent))}',
                     f': > {shlex.quote(destination)}']
        # Files can exceed a canonical serial line after the transport's second
        # base64 encoding. Decode independently aligned chunks into owned files.
        commands += [f"printf '%s' '{encoded[start:start + 1024]}' | base64 -d >> {shlex.quote(destination)}"
                     for start in range(0, len(encoded), 1024)]
    commands += [
        'chmod 755 /usr/local/libexec/uconsole-emulator-desktop',
        'chmod 755 /usr/local/libexec/uconsole-emulator-session',
        'sync',
    ]
    return ['set -eu; ' + command for command in commands]


def desktop_setup_script():
    """Standalone equivalent of the serial setup transactions."""
    return '\n'.join(desktop_setup_commands())


def wait_for_log(path, pattern, process, timeout, *, check_cancel=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_cancel:
            check_cancel()
        if process.poll() is not None:
            raise ValueError(f'QEMU stopped during desktop setup (exit {process.returncode}); see {path}')
        if path.exists() and pattern in path.read_bytes():
            return
        time.sleep(0.1)
    raise TimeoutError(f'Timed out waiting for maintenance shell; see {path}')


def configure_display(args):
    """Configure the writable guest overlay for Xorg on the surrogate fbdev."""
    with WorkspaceLock(args.workspace) as lock:
        return _configure_display_locked(args, lock)


def _configure_display_locked(args, lock):
    workspace = args.workspace.resolve()
    config = read_config(workspace)
    if config.get('surrogate_desktop', {}).get('schema') == SURROGATE_SCHEMA:
        print(f'Surrogate desktop already configured in {workspace}')
        return
    config = refresh_boot(workspace, args.qemu_img)
    # A private directory makes the socket endpoints capabilities belonging to
    # this launch. Use a short path to stay below sockaddr_un limits on macOS.
    with tempfile.TemporaryDirectory(prefix='uc-setup-', dir='/tmp' if os.name != 'nt' else None) as directory:
        _configure_display(args, workspace, config, Path(directory), lock)


def _configure_display(args, workspace, config, control_dir, lock):
    launch = parser().parse_args([
        '--workspace', str(workspace), '--qemu', args.qemu, 'run',
        '--mode', 'maintenance', '--qmp-port', str(args.qmp_port),
        '--serial-port', str(args.serial_port), '--display', 'none'])
    operation = uuid.uuid4().hex
    log = workspace / f'display-setup-{operation}-qemu.log'
    serial = workspace / f'display-setup-{operation}-serial.log'
    with log.open('xb') as output:
        process = subprocess.Popen(command(launch, control_dir, serial), stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, pass_fds=(lock.fileno(),))
    try:
        wait_for_log(serial, b'root@(none):/#', process, args.timeout)
        with control_connection(control_dir / 'serial') as connection:
            for setup_command in desktop_setup_commands():
                shell_command(connection, setup_command, timeout=30)
            shell_command(connection,
                "mountpoint -q /proc || mount -t proc proc /proc; sync; "
                "mountpoint -q /sys || mount -t sysfs sys /sys; "
                "rootmm=$(awk '$5 == \"/\" {print $3; exit}' /proc/self/mountinfo); "
                "rootdev=/dev/$(sed -n 's/^DEVNAME=//p' /sys/dev/block/$rootmm/uevent); "
                'test -b "$rootdev" && mount -o remount,ro "$rootdev" /', timeout=30)
        qmp(control_dir / 'qmp', 'quit')
        process.wait(timeout=10)
        if process.returncode:
            raise ValueError(f'QEMU desktop setup exited {process.returncode}; see {log}')
    except BaseException:
        if process.poll() is None:
            # An occupied QMP port may belong to another VM. On failure only
            # signal the child we created, never an unverified network endpoint.
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        raise
    config['surrogate_desktop'] = {
        'schema': SURROGATE_SCHEMA, 'backend': 'xorg-fbdev',
        'width': SURROGATE_WIDTH, 'height': SURROGATE_HEIGHT,
    }
    temporary = workspace / 'machine.json.tmp'
    temporary.write_text(json.dumps(config, indent=2) + '\n')
    temporary.replace(workspace / 'machine.json')
    print(f'Configured {workspace} for the {SURROGATE_WIDTH}x{SURROGATE_HEIGHT} Xorg/fbdev desktop')


def control_connection(endpoint):
    """Connect to a private Unix socket or a legacy loopback TCP port."""
    if isinstance(endpoint, int):
        return socket.create_connection(('127.0.0.1', endpoint), timeout=5)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect(str(endpoint))
        return sock
    except BaseException:
        sock.close()
        raise


def _qmp_io(operation, phase, callback, *args):
    """Identify an uncertain exchange without logging arguments or replaying it."""
    try:
        return callback(*args)
    except TimeoutError as exc:
        raise TimeoutError(f'QMP {operation}: timeout during {phase}; not retried') from exc


def qmp(port, operation, arguments=None):
    with _qmp_io(operation, 'connect', control_connection, port) as sock:
        with sock.makefile('rwb', buffering=0) as stream:
            # QEMU can flush a queued asynchronous event on reconnect before
            # its new greeting. Consume events only: never accept a response
            # as a greeting or resend an uncertain command. Bound both time
            # and message count so an event stream cannot stall negotiation.
            deadline = time.monotonic() + 5
            for _ in range(64):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('QMP greeting deadline')
                sock.settimeout(remaining)
                line = _qmp_io(operation, 'greeting', stream.readline)
                if not line:
                    raise ConnectionError('QMP closed before greeting')
                greeting = json.loads(line)
                if isinstance(greeting, dict) and isinstance(greeting.get('QMP'), dict):
                    break
                if (not isinstance(greeting, dict) or
                        not isinstance(greeting.get('event'), str) or
                        any(key in greeting for key in ('id', 'return', 'error', 'QMP'))):
                    raise ValueError('Endpoint is not a QMP monitor: expected greeting, received ' + repr(greeting)[:512])
            else:
                raise ValueError('Too many QMP events before greeting')
            sock.settimeout(5)
            for index, (name, params) in enumerate([('qmp_capabilities', {}), (operation, arguments or {})]):
                _qmp_io(operation, name + ' send', stream.write,
                        (json.dumps({'execute': name, 'arguments': params, 'id': index}) + '\n').encode())
                while True:
                    line = _qmp_io(operation, name + ' reply', stream.readline)
                    if not line:
                        raise ConnectionError('QMP closed before replying')
                    response = json.loads(line)
                    if name == 'quit' and response.get('event') == 'SHUTDOWN':
                        return {}
                    if response.get('id') == index:
                        break
                if 'error' in response:
                    raise ValueError(response['error'])
            return response['return']


def export(args):
    require_private_image_host()
    with WorkspaceLock(args.workspace):
        return _export_locked(args)


def _export_locked(args):
    from forge_filesystem import check_export_root
    from forge_export_capacity import source_capacity, trim_copy
    workspace = args.workspace.resolve()
    config = read_config(workspace)
    # qemu-img refuses its exclusive lock if QEMU is using the overlay.
    # Convert to a temporary file in the destination directory, then publish
    # without overwriting an existing image (including our own backing file).
    target = args.output.resolve()
    if target.exists():
        raise ValueError('Export destination already exists')
    capacity = source_capacity(config, workspace / 'base.img')
    temporary = target.with_name(target.name + '.partial')
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
    try:
        subprocess.run([args.qemu_img, 'convert', '-f', 'qcow2', '-O', 'raw',
                        str(workspace / 'disk.qcow2'), str(temporary)], check=True)
        if capacity is not None:
            trim_copy(temporary, capacity)
        check_export_root(temporary)
        sync_file(temporary)
        os.link(temporary, target)
        sync_directory(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    print(f'Exported {target}; sha256={digest(target)}')


def shell_command(sock, script, timeout=30):
    """Run a command on an already logged-in Linux shell; require its exit code.

    Base64 keeps quotes, newlines, and terminal control bytes out of the input
    line. Short commands only: Linux canonical TTY input has a bounded buffer.
    """
    marker = 'UC_' + uuid.uuid4().hex
    encoded = base64.b64encode(script.encode()).decode()
    line = f"printf '%s' '{encoded}' | base64 -d | /bin/bash; printf '\\n{marker}:%s\\n' $?\n"
    if len(line) > 3500:
        raise ValueError('Serial command exceeds canonical TTY line limit')
    sock.settimeout(timeout)
    sock.sendall(line.encode())
    end = time.monotonic() + timeout
    output = b''
    token = ('\n' + marker + ':').encode()
    while time.monotonic() < end:
        sock.settimeout(max(0.1, end - time.monotonic()))
        chunk = sock.recv(65536)
        if not chunk:
            raise ConnectionError('Guest serial console closed')
        output += chunk
        if token in output:
            result = output.split(token, 1)[1]
            if b'\n' in result:
                code = int(result.split(b'\n', 1)[0].strip())
                if code:
                    raise ValueError(f'Guest command failed ({code}): {output.decode(errors="replace")}')
                return output.decode(errors='replace')
    raise TimeoutError('Guest shell did not acknowledge command')


def put(args):
    payload = args.source.read_bytes()
    if len(payload) > 1024 * 1024:
        raise ValueError('Serial upload is limited to 1 MiB; use SSH/SCP for larger files')
    if not args.destination.startswith('/') or '\0' in args.destination:
        raise ValueError('Guest destination must be an absolute Linux path')
    temporary = '/tmp/uconsole-upload-' + uuid.uuid4().hex
    with socket.create_connection(('127.0.0.1', args.serial_port), timeout=5) as sock:
        # Prove a live shell before beginning a transaction.
        shell_command(sock, 'test "$(id -u)" = 0')
        try:
            shell_command(sock, f'umask 077; : > {temporary}')
            for pos in range(0, len(payload), 1024):
                encoded = base64.b64encode(payload[pos:pos + 1024]).decode()
                shell_command(sock, f"printf '%s' '{encoded}' | base64 -d >> {temporary}")
            expected = hashlib.sha256(payload).hexdigest()
            dest = shlex.quote(args.destination)
            output = shell_command(sock, f"test \"$(sha256sum {temporary} | cut -d' ' -f1)\" = {expected} && "
                                   f'install -m 644 {temporary} {dest} && sync && sha256sum {dest}')
            print(output)
        finally:
            shell_command(sock, f'rm -f {temporary}')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--workspace', type=Path, default=DEFAULT)
    p.add_argument('--qemu', default=executable('qemu-system-aarch64'))
    p.add_argument('--qemu-img', default=executable('qemu-img'))
    sub = p.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare', help='Create an isolated writable workspace')
    prep.add_argument('image', type=Path)
    prep.add_argument('--sha256')
    prep.set_defaults(func=prepare)
    launch = sub.add_parser('run', help='Boot the CM4 image (partial hardware coverage)')
    launch.add_argument('--mode', choices=['desktop', 'normal', 'maintenance'], default='normal')
    launch.add_argument('--audio', choices=AUDIO_MODES, default='none',
                        help='USB playback, synthetic capture, or simultaneous private-WAV/capture; no host microphone/native audio')
    launch.add_argument('--adc-reference', choices=['fixed', 'missing'], default='fixed',
                        help='ADC guest reference-supply profile; missing does not imply an absent converter')
    launch.add_argument('--keyboard', choices=['generic', 'composite'], default='generic',
                        help='experimental composite transport has no host input mapping or DFU yet')
    launch.add_argument('--serial-port', type=int)
    launch.add_argument('--qmp-port', type=int, default=4444)
    launch.add_argument('--gdb-port', type=int)
    launch.add_argument('--ssh-port', type=int)
    launch.add_argument('--keyboard-cdc-port', type=int,
                        help='split-device surrogate for the STM32 USB CDC function')
    launch.add_argument('--modem', choices=['none', 'composite'], default='none',
                        help='Composite modem prototype (requires --managed)')
    launch.add_argument('--modem-at-port', type=int,
                        help='split-device surrogate for one optional modem AT serial port')
    launch.add_argument('--vnc-display', type=int)
    launch.add_argument('--display', choices=DISPLAY_BACKENDS, default='none',
                        help='optional local QEMU framebuffer window (not DSI emulation)')
    launch.add_argument('--pause', action='store_true')
    launch.add_argument('--scenario', type=Path,
                        help='Versioned initial device state; requires --managed and verified QMP readback')
    launch.add_argument('--dry-run', action='store_true')
    launch.add_argument('--managed', action='store_true',
                        help='Use the shared owned runtime and private control sockets (no serial stdio)')
    launch.set_defaults(func=run)
    setup = sub.add_parser('configure-display',
                           help='Configure this overlay for the Xorg/fbdev surrogate desktop')
    setup.add_argument('--serial-port', type=int, default=4445)
    setup.add_argument('--qmp-port', type=int, default=4444)
    setup.add_argument('--timeout', type=int, default=90)
    setup.set_defaults(func=configure_display)
    control = sub.add_parser('control', help='Control an existing QEMU process')
    control.add_argument('operation', choices=['query-status', 'stop', 'cont', 'system_reset', 'quit'])
    control.add_argument('--qmp-port', type=int, default=4444)
    control.add_argument('--qmp-socket', type=Path, help='Managed runtime QMP socket')
    control.add_argument('--runtime-id', help='Required identity when using --qmp-socket')
    control.set_defaults(func=control_command)
    output = sub.add_parser('export', help='Export a stopped, cleanly shut down overlay to raw SD image')
    output.add_argument('output', type=Path)
    output.set_defaults(func=export)
    for action in ('checkpoint', 'restore'):
        lifecycle = sub.add_parser(action, help=f'{action.capitalize()} a stopped workspace')
        lifecycle.add_argument('name', help='Checkpoint name (letters, digits, dot, underscore, hyphen)')
        lifecycle.set_defaults(func=lambda args: print(json.dumps(
            getattr(Workspace(args.workspace, args.qemu_img), args.action)(args.name), indent=2)))
    recovery = sub.add_parser('recover', help='Finish an interrupted checkpoint restore')
    recovery.set_defaults(func=lambda args: print(json.dumps(
        Workspace(args.workspace, args.qemu_img).recover(), indent=2)))
    refresh = sub.add_parser('refresh-boot', help='Extract current guest kernel and regenerate emulator DTB')
    refresh.set_defaults(func=refresh_boot_command)
    upload = sub.add_parser('put', help='Copy a host file into a running maintenance shell (root)')
    upload.add_argument('source', type=Path)
    upload.add_argument('destination', help='Absolute guest path; overwrites that file')
    upload.add_argument('--serial-port', type=int, default=4445)
    upload.set_defaults(func=put)
    return p


def refresh_boot_command(args):
    with WorkspaceLock(args.workspace):
        print(json.dumps(refresh_boot(args.workspace, args.qemu_img), indent=2))


def control_command(args):
    endpoint = args.qmp_socket or args.qmp_port
    if args.qmp_socket:
        if not args.runtime_id:
            raise ValueError('--qmp-socket requires --runtime-id from the managed launch')
        if qmp(endpoint, 'query-name').get('name') != args.runtime_id:
            raise ValueError('QMP runtime identity mismatch')
    print(json.dumps(qmp(endpoint, args.operation)))


def main():
    args = parser().parse_args()
    try:
        return args.func(args) or 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
