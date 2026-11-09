"""Image preservation, DTB edits, and host-independent launch contract."""
import importlib.util
import base64
import configparser
import io
import json
import os
from pathlib import Path
import shlex
import struct
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from emulator_image import BootPartition
from emulator_dtb import patch_strings
import uconsole_emulator as emulator
import uconsole_agent as agent


def sample_dtb():
    def word(*args):
        return struct.pack('>' + 'I' * len(args), *args)
    structure = word(1) + b'\0' * 4 + word(1) + b'soc\0'
    structure += word(3, 5, 0) + b'okay\0\0\0\0' + word(2, 2, 9)
    strings = b'status\0'
    return word(0xd00dfeed, 56 + len(structure) + len(strings), 56,
                56 + len(structure), 40, 17, 16, 0, len(strings), len(structure)) + b'\0' * 16 + structure + strings


class EmulatorTests(unittest.TestCase):
    def test_dtb_add_delete_and_change(self):
        original = sample_dtb()
        changed = patch_strings(original, {('/soc', 'status'): 'disabled', ('/soc', 'dr_mode'): 'host'})
        self.assertIn(b'disabled\0', changed)
        self.assertIn(b'host\0', changed)
        deleted = patch_strings(changed, {('/soc', 'status'): None})
        self.assertNotIn(b'disabled\0', deleted)
        self.assertEqual(struct.unpack_from('>I', deleted, 4)[0], len(deleted))
        self.assertEqual(patch_strings(original, {}), original)
        with self.assertRaises(ValueError):
            patch_strings(original, {('/nonexistent', 'status'): 'okay'})

    def test_fat16_extraction_and_cycle_rejection(self):
        data = bytearray(512 * 100)
        data[510:512] = b'\x55\xaa'
        data[450] = 6
        struct.pack_into('<II', data, 454, 1, 99)
        struct.pack_into('<H', data, 512 + 11, 512)
        data[512 + 13] = 1
        struct.pack_into('<H', data, 512 + 14, 1)
        data[512 + 16] = 1
        struct.pack_into('<H', data, 512 + 17, 16)
        struct.pack_into('<H', data, 512 + 22, 1)
        struct.pack_into('<H', data, 1024 + 4, 0xffff)
        data[1536:1547] = b'KERNEL8 IMG'
        struct.pack_into('<H', data, 1536 + 26, 2)
        struct.pack_into('<I', data, 1536 + 28, 5)
        data[2048:2053] = b'hello'
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'sd.img'
            target = Path(directory) / 'kernel.img'
            image.write_bytes(data)
            with BootPartition(image) as boot:
                boot.extract('kernel8.img', target)
                self.assertEqual(target.read_bytes(), b'hello')
                with self.assertRaises(FileExistsError):
                    boot.extract('kernel8.img', target)
                with self.assertRaises(FileNotFoundError):
                    boot.extract('missing.img', target)
            self.assertEqual(image.read_bytes(), data)
            struct.pack_into('<H', data, 1024 + 4, 2)
            image.write_bytes(data)
            with BootPartition(image) as boot:
                with self.assertRaisesRegex(ValueError, 'cyclic'):
                    list(boot.chain(2))

    def test_checksum_failure_creates_no_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'bad.bz2'
            image.write_bytes(b'not the expected image')
            workspace = Path(directory) / 'workspace'
            args = emulator.parser().parse_args(['--workspace', str(workspace), 'prepare', str(image)])
            # Isolate byte validation from the separately tested host gate.
            with patch.object(emulator, 'require_private_image_host'), self.assertRaisesRegex(ValueError, 'checksum'):
                emulator.prepare(args)
            self.assertFalse(workspace.exists())

    def test_launch_uses_overlay_and_loopback_only(self):
        with tempfile.TemporaryDirectory(prefix='cm4, test ') as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            args = emulator.parser().parse_args(['--workspace', directory, 'run', '--mode', 'maintenance',
                '--serial-port', '4445', '--gdb-port', '1234', '--ssh-port', '2222', '--vnc-display', '1',
                '--keyboard-cdc-port', '4550', '--modem-at-port', '4551'])
            cmd = emulator.command(args)
            self.assertIn('raspi4b', cmd)
            self.assertNotIn('virt', cmd)
            self.assertIn('init=/bin/bash', cmd[cmd.index('-append') + 1])
            self.assertIn('bcm2708_fb.fbwidth=1280', cmd[cmd.index('-append') + 1])
            self.assertIn('bcm2708_fb.fbheight=720', cmd[cmd.index('-append') + 1])
            self.assertIn('bcm2708_fb.fbdepth=32', cmd[cmd.index('-append') + 1])
            drive = cmd[cmd.index('-drive') + 1]
            self.assertIn('disk.qcow2', drive)
            self.assertIn('cm4,, test', drive)
            self.assertNotIn('base.img', drive)
            for flag in ['-qmp', '-gdb', '-vnc']:
                self.assertIn('127.0.0.1', cmd[cmd.index(flag) + 1])
            self.assertTrue(any('id=keyboard-cdc' in item for item in cmd))
            self.assertTrue(any('id=modem-at' in item for item in cmd))
            self.assertEqual(cmd[cmd.index('-display') + 1], 'none')

    def test_local_display_and_vnc_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            for backend in emulator.DISPLAY_BACKENDS:
                local = emulator.parser().parse_args(['--workspace', directory, 'run', '--display', backend])
                self.assertEqual(emulator.command(local)[emulator.command(local).index('-display') + 1], backend)
            conflict = emulator.parser().parse_args([
                '--workspace', directory, 'run', '--display', 'sdl', '--vnc-display', '1'])
            with self.assertRaisesRegex(ValueError, 'cannot be combined'):
                emulator.command(conflict)

    def test_native_display_uses_cocoa_on_macos_without_changing_headless_default(self):
        for platform, backend in (('darwin', 'cocoa'), ('linux', 'gtk')):
            with patch.object(emulator.sys, 'platform', platform):
                self.assertEqual(emulator.native_display(), backend)
                self.assertEqual(emulator.parser().parse_args(['run']).display, 'none')

    def test_adc_reference_profile_is_explicit_and_does_not_edit_guest_files(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'machine.json'
            config.write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            before = config.read_bytes()
            args = emulator.parser().parse_args(['--workspace', directory, 'run'])
            self.assertNotIn('adc101c.reference-supply-present=false', emulator.command(args))
            args = emulator.parser().parse_args(['--workspace', directory, 'run',
                                                '--adc-reference', 'missing'])
            command = emulator.command(args)
            self.assertEqual(command[command.index('-global') + 1],
                             'adc101c.reference-supply-present=false')
            self.assertEqual(config.read_bytes(), before)
            args.adc_reference = 'typo'
            with self.assertRaisesRegex(ValueError, 'reference profile'):
                emulator.command(args)

    def test_audio_is_opt_in_null_playback_without_host_device_access(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            args = emulator.parser().parse_args(['--workspace', directory, 'run'])
            self.assertNotIn('-audiodev', emulator.command(args))
            args.audio = 'usb-null'
            cmd = emulator.command(args)
            self.assertEqual(cmd[cmd.index('-audiodev') + 1], 'none,id=forge-audio')
            self.assertIn('usb-audio,id=audio-surrogate,audiodev=forge-audio', cmd)
            args.audio = 'usb-wav'
            with self.assertRaisesRegex(ValueError, 'owned runtime'):
                emulator.command(args)
            cmd = emulator.command(args, audio_output=Path(directory) / 'private,recording.wav')
            backend = cmd[cmd.index('-audiodev') + 1]
            self.assertIn('private,,recording.wav', backend)
            self.assertIn('out.frequency=48000,out.channels=2,out.format=s16', backend)
            args.audio = 'usb-capture'
            cmd = emulator.command(args)
            self.assertIn('usb-forge-capture,id=audio-surrogate', cmd)
            self.assertNotIn('-audiodev', cmd)
            self.assertNotIn('usb-audio,id=audio-surrogate,audiodev=forge-audio', cmd)
            args.audio = 'usb-duplex'
            with self.assertRaisesRegex(ValueError, 'owned runtime'):
                emulator.command(args)
            cmd = emulator.command(args, audio_output=Path(directory) / 'duplex.wav')
            self.assertIn('usb-audio,id=audio-surrogate,audiodev=forge-audio', cmd)
            self.assertIn('usb-forge-capture,id=audio-capture', cmd)
            self.assertEqual(cmd.count('-audiodev'), 1)
            self.assertTrue(cmd[cmd.index('-audiodev') + 1].startswith('wav,'))
            args.audio = 'host-microphone'
            with self.assertRaisesRegex(ValueError, 'Unsupported audio'):
                emulator.command(args)

    def test_composite_keyboard_is_opt_in_and_excludes_split_cdc(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            args = emulator.parser().parse_args(['--workspace', directory, 'run'])
            self.assertIn('usb-kbd', emulator.command(args))
            self.assertIn('usb-mouse', emulator.command(args))
            args.keyboard = 'composite'
            cmd = emulator.command(args)
            self.assertIn('usb-uconsole-keyboard,id=deck', cmd)
            self.assertNotIn('usb-kbd', cmd)
            self.assertNotIn('usb-mouse', cmd)
            args.keyboard_cdc_port = 4550
            with self.assertRaisesRegex(ValueError, 'split'):
                emulator.command(args)
            args.keyboard_cdc_port = None
            args.keyboard = 'unknown'
            with self.assertRaisesRegex(ValueError, 'Unsupported keyboard'):
                emulator.command(args)

    def test_desktop_mode_selects_graphical_target(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=12345678-02'}))
            args = emulator.parser().parse_args(['--workspace', directory, 'run', '--mode', 'desktop',
                                                 '--display', 'gtk'])
            command_line = emulator.command(args)[emulator.command(args).index('-append') + 1]
            self.assertIn('systemd.unit=graphical.target', command_line)
            self.assertIn('uconsole.emulator=1', command_line)
            self.assertIn('systemd.wants=uconsole-emulator-desktop.service', command_line)
            self.assertIn('systemd.mask=uconsole-4g-cm4.service', command_line)
            self.assertIn('systemd.mask=wayvnc.service', command_line)
            for service in ('lightdm', 'getty@tty1', 'userconfig'):
                self.assertNotIn(f'systemd.mask={service}.service', command_line)
            self.assertIn('systemd.mask=plymouth-start.service', command_line)
            self.assertIn('systemd.mask=plymouth-quit.service', command_line)
            self.assertIn('systemd.mask=plymouth-quit-wait.service', command_line)
            for mode in ('normal', 'maintenance'):
                args.mode = mode
                other = emulator.command(args)
                self.assertNotIn('systemd.mask=userconfig.service', other[other.index('-append') + 1])

    def test_desktop_setup_uses_fbdev_without_replacing_source_image(self):
        files = emulator.desktop_setup_files()
        content = '\n'.join(files.values())
        self.assertIn('Driver "fbdev"', content)
        self.assertIn('/dev/fb0', content)
        self.assertIn('/usr/bin/Xorg -config /etc/uconsole-emulator/xorg.conf -nolisten tcp', content)
        self.assertIn('/etc/systemd/system/uconsole-emulator-desktop.service', files)
        self.assertIn('ConditionKernelCommandLine=uconsole.emulator=1', content)
        self.assertEqual(files['/etc/systemd/system/getty@tty1.service.d/90-uconsole-emulator.conf'],
                         '[Unit]\nConditionKernelCommandLine=!uconsole.emulator=1\n')
        self.assertEqual(files['/etc/systemd/system/lightdm.service.d/90-uconsole-emulator.conf'],
                         '[Service]\nExecStart=\nExecStart=/usr/local/libexec/uconsole-emulator-desktop\n')
        self.assertIn('-config /etc/uconsole-emulator/xorg.conf', content)
        self.assertIn('Requires=lightdm.service', content)
        self.assertFalse(any(path.startswith('/etc/X11/xorg.') for path in files))
        script = emulator.desktop_setup_script()
        self.assertNotIn('ln -s', script)
        self.assertNotIn('rm -f', script)
        self.assertIn('Legacy emulator display defaults require recovery', script)

    def test_lightdm_adapter_preserves_native_config_and_refreshes_autologin(self):
        source = emulator.desktop_setup_files()['/usr/local/libexec/uconsole-emulator-desktop']
        # Map only the guest filesystem; execute the actual generated adapter.
        source = source.replace('from pathlib import Path\n', '')
        class Handoff(Exception):
            pass
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def guest_path(value):
                return root / str(value).lstrip('/')
            native = guest_path('/etc/lightdm/lightdm.conf')
            native.parent.mkdir(parents=True)
            cmdline = guest_path('/proc/cmdline')
            cmdline.parent.mkdir()
            guest_path('/run').mkdir()
            runtime = guest_path('/run/uconsole-emulator/lightdm.conf')
            native.write_text('[Seat:*]\nautologin-user=wizard\nuser-session=rpd-labwc\n')
            original = native.read_bytes()
            def run(marker):
                cmdline.write_text(marker)
                with patch('os.execv', side_effect=Handoff) as execute, patch('os.umask'), \
                     patch.object(sys, 'argv', ['adapter', '--debug']):
                    with self.assertRaises(Handoff):
                        exec(compile(source, '<guest-adapter>', 'exec'), {'Path': guest_path})
                return execute.call_args.args
            self.assertEqual(run('console=tty1'), ('/usr/sbin/lightdm', ['/usr/sbin/lightdm', '--debug']))
            self.assertFalse(runtime.exists())
            for user in ('wizard', 'forgeproof'):
                native.write_text(f'[Seat:*]\nautologin-user={user}\nuser-session=rpd-labwc\n')
                before = native.read_bytes()
                command = run('console=tty1 uconsole.emulator=1')
                self.assertEqual(command[1][1:], ['--config', str(runtime), '--debug'])
                config = configparser.ConfigParser()
                config.read(runtime)
                self.assertEqual(config['Seat:*']['autologin-user'], user)
                self.assertEqual(config['Seat:*']['user-session'], 'rpd-x')
                self.assertEqual(config['Seat:*']['autologin-session'], 'rpd-x')
                self.assertEqual(config['LightDM']['logind-check-graphical'], 'false')
                self.assertEqual(native.read_bytes(), before)

    def test_desktop_setup_transactions_fit_serial_line_limit(self):
        decoded = {}
        for command in emulator.desktop_setup_commands():
            encoded = base64.b64encode(command.encode()).decode()
            # Include the actual shell wrapper and a conservative marker budget.
            self.assertLess(len(encoded) + 150, 3500)
            tokens = shlex.split(command)
            if command.startswith('set -eu; : > '):
                decoded[tokens[-1]] = b''
            elif command.startswith("set -eu; printf '%s'"):
                decoded[tokens[-1]] += base64.b64decode(tokens[tokens.index('printf') + 2])
        self.assertEqual(decoded, {path: content.encode() for path, content in emulator.desktop_setup_files().items()})

    @unittest.skipUnless(os.name == 'posix', 'executes POSIX guest session adapter')
    def test_session_adapter_scopes_polkit_override_and_preserves_user_choice(self):
        files = emulator.desktop_setup_files()
        source = files['/usr/local/libexec/uconsole-emulator-session']
        source = source.replace('from pathlib import Path\n', '')
        self.assertIn('Hidden=true', files[
            '/etc/uconsole-emulator/xdg/autostart/polkit-mate-authentication-agent-1.desktop'])
        self.assertIn('uconsole.emulator=1', files['/etc/X11/Xsession.d/90uconsole-emulator'])
        self.assertFalse(any(path.startswith('/usr/share/xsessions/') for path in files))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def guest_path(value):
                return root / str(value).lstrip('/')
            guest_path.home = lambda: guest_path('/home/test')
            cmdline = guest_path('/proc/cmdline')
            cmdline.parent.mkdir()
            system = guest_path('/etc/xdg/lxsession/rpd-x/desktop.conf')
            system.parent.mkdir(parents=True)
            system.write_text('[Session]\npolkit/command=lxpolkit\n')
            personal = guest_path('/home/test/.config/lxsession/rpd-x/desktop.conf')
            personal.parent.mkdir(parents=True)
            runtime = guest_path('/run/user/' + str(os.getuid()))
            runtime.mkdir(mode=0o700, parents=True)
            startup = guest_path('/etc/xdg/autostart/keep.desktop')
            startup.parent.mkdir()
            startup.write_text('[Desktop Entry]\nExec=keep\n')

            def native_files():
                return {str(p): p.read_bytes() for p in root.rglob('*')
                        if p.is_file() and not p.is_relative_to(runtime)}

            def run(marker, environment=None, available=True):
                environment = dict({'DESKTOP_SESSION': 'rpd-x'}, **(environment or {}))
                cmdline.write_text(marker)
                before = native_files()
                output = io.StringIO()
                with patch.dict('os.environ', environment or {}, clear=True), \
                     patch('shutil.which', return_value='/usr/bin/lxpolkit' if available else None), \
                     patch.object(sys, 'stdout', output):
                    exec(compile(source, '<guest-session>', 'exec'), {'Path': guest_path})
                    self.assertEqual(dict(os.environ), environment or {})
                self.assertEqual(before, native_files())
                return output.getvalue().strip()

            def assert_profile(output):
                profile, original = output.split(':', 1)
                profile = Path(profile)
                self.assertEqual(profile.parent, runtime)
                self.assertEqual(profile.stat().st_mode & 0o777, 0o700)
                self.assertEqual(original, str(guest_path('/etc/xdg')))
                self.assertEqual((profile / 'lxsession').resolve(), system.parent.parent.resolve())
                self.assertEqual((profile / 'autostart/keep.desktop').read_bytes(), startup.read_bytes())
                self.assertTrue((profile / 'autostart/polkit-mate-authentication-agent-1.desktop').is_symlink())

            for marker in ('console=tty1', 'uconsole.emulator=10'):
                original = {'XDG_CONFIG_DIRS': '/opt/user-config:/etc/xdg'}
                self.assertEqual(run(marker, original), '')
            assert_profile(run('uconsole.emulator=1'))
            self.assertEqual(run('uconsole.emulator=1', available=False), '')
            self.assertEqual(run('uconsole.emulator=1', {'DESKTOP_SESSION': 'other'}), '')
            personal.write_text('[Session]\npolkit/command=custom-agent\n')
            self.assertEqual(run('uconsole.emulator=1'), '')
            personal.write_text('[Session]\nwindow_manager=custom-wm\n')
            assert_profile(run('uconsole.emulator=1'))
            runtime.chmod(0o755)
            self.assertEqual(run('uconsole.emulator=1'), '')
            runtime.chmod(0o700)
            personal.write_text('malformed config without section\n')
            self.assertEqual(run('uconsole.emulator=1'), '')

    @unittest.skipUnless(os.name == 'posix' and Path('/bin/sh').exists(), 'executes POSIX guest Xsession hook')
    def test_xsession_hook_only_exports_on_exact_emulator_marker(self):
        source = emulator.desktop_setup_files()['/etc/X11/Xsession.d/90uconsole-emulator']
        for marker, expected in [('console=tty1', '/custom'),
                                 ('uconsole.emulator=10', '/custom'),
                                 ('console=tty1 uconsole.emulator=1', '/overlay:/custom')]:
            hook = source.replace('cat /proc/cmdline', "printf '%s' " + shlex.quote(marker))
            hook = hook.replace('/usr/local/libexec/uconsole-emulator-session',
                                "printf '%s' '/overlay:/custom'")
            hook += '\nprintf "%s:%s" "$XDG_CONFIG_DIRS" "${UCONSOLE_SESSION_CONFIG_DIRS+leaked}"\n'
            result = emulator.subprocess.run(['/bin/sh', '-c', hook],
                env=dict(os.environ, XDG_CONFIG_DIRS='/custom'),
                capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, expected + ':')
            self.assertEqual(result.stderr, '')
        failing = source.replace('cat /proc/cmdline', "printf '%s' uconsole.emulator=1")
        failing = failing.replace('/usr/local/libexec/uconsole-emulator-session', 'false')
        failing += '\nprintf "%s" "$XDG_CONFIG_DIRS"\n'
        result = emulator.subprocess.run(['/bin/sh', '-ec', failing],
            env=dict(os.environ, XDG_CONFIG_DIRS='/custom'), capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, '/custom')

    def test_setup_interrupt_cleans_up_owned_child(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            config = {'root': 'PARTUUID=12345678-02'}
            (workspace / 'machine.json').write_text(json.dumps(config))
            args = emulator.parser().parse_args(['--workspace', directory, 'configure-display'])
            process = MagicMock()
            process.poll.return_value = None
            with patch.object(emulator.subprocess, 'Popen', return_value=process), \
                 patch.object(emulator, 'refresh_boot', return_value=config), \
                 patch.object(emulator, 'wait_for_log', side_effect=KeyboardInterrupt), \
                 patch.object(emulator, 'qmp') as control, self.assertRaises(KeyboardInterrupt):
                emulator.configure_display(args)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=10)
            control.assert_not_called()
            self.assertNotIn('surrogate_desktop', json.loads((workspace / 'machine.json').read_text()))

    def test_setup_controls_are_private_and_preserve_running_serial_log(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            original = json.dumps({'root': 'PARTUUID=12345678-02'})
            (workspace / 'machine.json').write_text(original)
            serial = workspace / 'serial.log'
            serial.write_text('existing VM evidence')
            args = emulator.parser().parse_args([
                '--workspace', directory, 'configure-display'])
            process = MagicMock()
            process.poll.return_value = None
            with patch.object(emulator.subprocess, 'Popen', return_value=process) as spawn, \
                 patch.object(emulator, 'refresh_boot', return_value=json.loads(original)), \
                 patch.object(emulator, 'wait_for_log', side_effect=TimeoutError('fixture')), \
                 patch.object(emulator, 'qmp') as control:
                with self.assertRaises(TimeoutError):
                    emulator.configure_display(args)
                control.assert_not_called()
                process.terminate.assert_called_once()
                cmd = spawn.call_args.args[0]
                endpoint = cmd[cmd.index('-qmp') + 1]
                self.assertTrue(endpoint.startswith('unix:'))
                self.assertIn('uc-setup-', endpoint)
                chardev = cmd[cmd.index('-chardev') + 1]
                self.assertIn('path=', chardev)
                self.assertIn('uc-setup-', chardev)
                self.assertNotIn('host=', chardev)
                self.assertIn('display-setup-', chardev)
            self.assertEqual(serial.read_text(), 'existing VM evidence')
            self.assertEqual((workspace / 'machine.json').read_text(), original)

    def test_normal_boot_does_not_select_emulator_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'machine.json').write_text(json.dumps({'root': '/dev/mmcblk0p2'}))
            args = emulator.parser().parse_args(['--workspace', directory, 'run'])
            cmd = emulator.command(args)
            cmdline = cmd[cmd.index('-append') + 1]
            self.assertNotIn('uconsole.emulator=1', cmdline)
            self.assertNotIn('systemd.wants=uconsole-emulator-desktop', cmdline)

    def test_normal_and_desktop_boot_allow_early_root_fsck(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'machine.json').write_text(json.dumps({'root': 'PARTUUID=1234-02'}))
            for mode in ('normal', 'desktop', 'maintenance'):
                args = emulator.parser().parse_args(['--workspace', directory, 'run', '--mode', mode])
                command = emulator.command(args)
                words = command[command.index('-append') + 1].split()
                writable = mode == 'maintenance'
                self.assertEqual('rw' in words, writable)
                self.assertEqual('ro' in words, not writable)

    def test_actual_launch_never_falls_back_when_refresh_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            args = emulator.parser().parse_args(['--workspace', directory, 'run'])
            with patch.object(emulator.subprocess, 'check_output', return_value='raspi4b machine'), \
                 patch.object(emulator, 'refresh_boot', side_effect=ValueError('incompatible kernel')), \
                 patch.object(emulator.subprocess, 'call') as launch:
                with self.assertRaisesRegex(ValueError, 'incompatible kernel'):
                    emulator.run(args)
                launch.assert_not_called()

    def test_export_never_overwrites_existing_image(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text('{}')
            target = workspace / 'base.img'
            target.write_bytes(b'preserve')
            args = emulator.parser().parse_args(['--workspace', directory, 'export', str(target)])
            # Exercise overwrite refusal even on hosts whose image API is gated.
            with patch.object(emulator, 'require_private_image_host'), self.assertRaisesRegex(ValueError, 'already exists'):
                emulator.export(args)
            self.assertEqual(target.read_bytes(), b'preserve')

    def test_agent_context_is_clean_and_explicit_about_limits(self):
        state = {'workspace': '/tmp/work', 'machine': {'machine': 'raspi4b',
                 'coverage': 'partial-cm4', 'root': 'PARTUUID=1234-02',
                 'source_sha256': 'abc'}, 'runtime': {'status': 'running'},
                 'serial_tail': ['\x1b[31mbooted\x1b[0m']}
        text = agent.context_markdown(state)
        self.assertIn('partial-cm4', text)
        self.assertIn('Known fidelity limits', text)
        self.assertNotIn('\x1b', agent.clean(text))

    def test_private_images_reject_missing_permissions_before_workspace_access(self):
        with patch.object(emulator.os, 'fchmod', None, create=True), \
             patch.object(emulator, 'WorkspaceLock') as lock:
            for operation in (emulator.prepare, emulator.export):
                with self.assertRaisesRegex(RuntimeError, 'POSIX file permissions'):
                    operation(None)
            lock.assert_not_called()

    @unittest.skipUnless(callable(getattr(os, 'fchmod', None)), 'private image export requires POSIX permissions')
    def test_export_flush_failure_never_publishes_image(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / 'machine.json').write_text('{}')
            target = workspace / 'export.img'
            args = emulator.parser().parse_args(['--workspace', directory, 'export', str(target)])

            def convert(command, **kwargs):
                Path(command[-1]).write_bytes(b'converted image')

            with patch.object(emulator.subprocess, 'run', side_effect=convert), \
                 patch('forge_filesystem.check_export_root'), \
                 patch.object(emulator, 'sync_file', side_effect=OSError('disk flush failed')):
                with self.assertRaisesRegex(OSError, 'disk flush failed'):
                    emulator.export(args)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name('export.img.partial').exists())

    def test_agent_removes_osc_hyperlinks_with_st_or_bell_terminators(self):
        for terminator in ('\x1b\\', '\x07'):
            link = '\x1b]8;;file://guest/etc/service' + terminator
            end = '\x1b]8;;' + terminator
            self.assertEqual(agent.clean('Loaded: ' + link + 'service' + end + '\r\n'),
                             'Loaded: service\n')

    def test_guest_commands_disable_interactive_pagers(self):
        with patch.object(agent, 'control_connection') as connect, \
             patch.object(agent.uuid, 'uuid4', return_value=MagicMock(hex='fixture')):
            sock = connect.return_value.__enter__.return_value
            sock.recv.return_value = (b'\nUC_AGENT_fixture_BEGIN\nok\n'
                                      b'UC_AGENT_fixture_END:0:UC_AGENT_fixture_DONE\n')
            result = agent.serial_exec(1234, 'busctl introspect example /')
            wire = sock.sendall.call_args.args[0]
        self.assertEqual(result, {'exit_code': 0, 'stdout': 'ok'})
        self.assertIn(b'SYSTEMD_PAGER=cat PAGER=cat SYSTEMD_COLORS=0 /bin/bash', wire)

    def test_serial_completion_uses_full_nonce_frame_not_trailing_newline(self):
        for suffix in (b'\r\n', b'[    2.5] kernel message\r\n'):
            with self.subTest(suffix=suffix), \
                    patch.object(agent, 'control_connection') as connect, \
                    patch.object(agent.uuid, 'uuid4', return_value=MagicMock(hex='fixture')):
                sock = connect.return_value.__enter__.return_value
                sock.recv.side_effect = [
                    b'\nUC_AGENT_fixture_BEGIN\noutput\nUC_AGENT_fixture_END:1',
                    b'27:UC_AGENT_fixture_DO', b'NE' + suffix]
                self.assertEqual(agent.serial_exec(1234, 'exit 127'),
                                 {'exit_code': 127, 'stdout': 'output'})
                self.assertEqual(sock.recv.call_count, 3)
        for frame in (b'0\n', b'0:UC_AGENT_other_DONE', b'0:UC_AGENT_fixture_DO'):
            with self.subTest(frame=frame), \
                    patch.object(agent, 'control_connection') as connect, \
                    patch.object(agent.uuid, 'uuid4', return_value=MagicMock(hex='fixture')):
                sock = connect.return_value.__enter__.return_value
                sock.recv.side_effect = [b'\nUC_AGENT_fixture_BEGIN\nok\nUC_AGENT_fixture_END:' + frame, b'']
                with self.assertRaises(agent.GuestChannelUncertain):
                    agent.serial_exec(1234, 'true')

    def test_dispatched_serial_failures_are_uncertain_not_completion(self):
        for failure in ('timeout', 'eof', 'output-limit', 'partial-send', 'interrupt'):
            with self.subTest(failure=failure), \
                 patch.object(agent, 'control_connection') as connect, \
                 patch.object(agent, 'MAX_SERIAL_OUTPUT', 8):
                sock = connect.return_value.__enter__.return_value
                if failure == 'timeout':
                    sock.recv.side_effect = TimeoutError('fixture deadline')
                elif failure == 'eof':
                    sock.recv.return_value = b''
                elif failure == 'output-limit':
                    sock.recv.return_value = b'x' * 9
                elif failure == 'interrupt':
                    sock.recv.side_effect = KeyboardInterrupt()
                else:
                    sock.sendall.side_effect = BrokenPipeError('partial write')
                with self.assertRaisesRegex(agent.GuestChannelUncertain, 'may still be running'):
                    agent.serial_exec(1234, 'sleep 60')
                sock.sendall.assert_called_once()

    def test_upload_does_not_send_cleanup_after_uncertain_chunk(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.write_text('x')
            ok = {'exit_code': 0, 'stdout': ''}
            with patch.object(agent, 'serial_exec', side_effect=[ok, ok,
                              agent.GuestChannelUncertain('chunk still running')]) as execute:
                with self.assertRaises(agent.GuestChannelUncertain):
                    agent.guest_put(1234, source, '/tmp/destination')
                self.assertEqual(execute.call_count, 3)
                self.assertFalse(any(c.args[1].startswith('rm -f') for c in execute.call_args_list))

    def test_upload_reports_uncertain_cleanup_after_committed_install(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source'
            source.write_text('x')
            ok = {'exit_code': 0, 'stdout': ''}
            with patch.object(agent, 'serial_exec', side_effect=[ok] * 4 + [
                              agent.GuestChannelUncertain('cleanup unacknowledged')]) as execute:
                with self.assertRaisesRegex(agent.GuestChannelUncertain, 'cleanup unacknowledged'):
                    agent.guest_put(1234, source, '/tmp/destination')
                self.assertIn('install -m', execute.call_args_list[3].args[1])
                self.assertTrue(execute.call_args_list[4].args[1].startswith('rm -f'))

    def test_task_schema_and_host_argv_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            task_file = Path(directory) / 'tasks.json'
            task_file.write_text(json.dumps({'schema': 1, 'tasks': {
                'answer': {'kind': 'host', 'argv': [sys.executable, '-c', 'print(42)']}}}))
            tasks = agent.load_tasks(task_file)
            result = agent.run_task('answer', tasks['answer'], 1)
            self.assertEqual(result['exit_code'], 0)
            self.assertEqual(result['stdout'], '42\n')

    def test_hardware_probe_comparison_reports_changed_probes(self):
        from uconsole_hardware_probe import compare, run_command
        first = {'target': 'emulator', 'probes': {'model': {'exit_code': 0, 'stdout': 'CM4'}}}
        second = {'target': 'hardware', 'probes': {'model': {'exit_code': 0, 'stdout': 'uConsole'}}}
        result = compare(first, second)
        self.assertFalse(result['matching'])
        self.assertEqual(result['differences'][0]['probe'], 'model')
        missing = run_command(['/definitely/missing/uconsole-probe'], None)
        self.assertIsNone(missing['exit_code'])
        self.assertTrue(missing['stderr'])

    def test_hardware_probe_is_passive_and_failed_captures_are_not_matches(self):
        from uconsole_hardware_probe import PROBES, compare
        self.assertNotIn('i2cdetect', ' '.join(PROBES['i2c']))
        self.assertIn('/sys/bus/i2c/devices/', ' '.join(PROBES['i2c']))
        for exit_code in (None, 1, 127):
            failed = {'probes': {'i2c': {'exit_code': exit_code, 'stdout': ''}}}
            result = compare(failed, failed)
            self.assertFalse(result['matching'])
            self.assertFalse(result['complete'])
            self.assertEqual(result['incomplete_probes'], ['i2c'])
        self.assertFalse(compare({'probes': {}}, {'probes': {}})['matching'])
        good = {'probes': {'model': {'exit_code': 0, 'stdout': 'CM4'}}}
        self.assertTrue(compare(good, good)['matching'])
        missing = compare(good, {'probes': {}})
        self.assertFalse(missing['complete'])
        self.assertEqual(missing['incomplete_probes'], ['model'])


if __name__ == '__main__':
    unittest.main()
