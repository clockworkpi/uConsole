import unittest
import base64
from pathlib import Path
import struct
import tempfile

from validate_recovery_watchdog import verify_expiry, storage_fixture, run, network_samples, server_key


class RecoveryWatchdogValidationTests(unittest.TestCase):
    def test_explicit_server_key_is_single_valid_ed25519_key(self):
        wire = struct.pack('>I', 11)+b'ssh-ed25519'+struct.pack('>I', 32)+b'x'*32
        public = 'ssh-ed25519 '+base64.b64encode(wire).decode()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'server.pub'
            path.write_text(public+' recovery server\n')
            self.assertEqual(server_key(None, path), public)
            for invalid in ('', public+'\n'+public, 'ssh-rsa AAAA', 'ssh-ed25519 AAAA', 'x'*4097):
                path.write_text(invalid)
                with self.assertRaises(ValueError):
                    server_key(None, path)
    def test_network_samples_accept_only_bounded_boolean_records(self):
        line = ('Forge recovery network: present=1 ipv4=1 associated_seen=0 '
                'assoc_reject_seen=0 auth_disabled_seen=0 lease_bound=1\n')
        self.assertEqual(network_samples(line)[0]['lease_bound'], 1)
        for bad in ('', line*7, line+'private-ssid\n', line.replace('ipv4=1', 'ipv4=2')):
            with self.assertRaises(ValueError):
                network_samples(bad)

    def test_storage_fixture_is_exclusive_and_has_exact_bounded_partitions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sd.img'
            fixture = storage_fixture(path)
            self.assertEqual(path.stat().st_size,64*1024*1024)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            with path.open('rb') as stream:
                header = stream.read(512)
            self.assertEqual(struct.unpack_from('<II',header,454),(8192,8192))
            self.assertEqual(struct.unpack_from('<II',header,470),(16384,114688))
            self.assertEqual(fixture['root_offset']+fixture['root_length'],fixture['bytes'])
            with self.assertRaises(FileExistsError):
                storage_fixture(path)

    def test_storage_requires_python_before_any_output_or_image_access(self):
        with self.assertRaises(ValueError):
            run(None,None,None,None,None,None,None,storage=True)
        with self.assertRaises(ValueError):
            run(None,None,None,None,None,None,None,interrupt_backup=True)

    def test_exit_and_window_required(self):
        verify_expiry(0, 15, 'watchdog left running')
        for code, seconds in ((1, 15), (-15, 15), (0, 0), (0, 300), (0, float('nan'))):
            with self.assertRaises(RuntimeError):
                verify_expiry(code, seconds, '')

    def test_software_reboot_panic_and_external_kill_are_not_watchdog_evidence(self):
        for marker in ('Kernel panic', 'Forge recovery failed;', 'Forge recovery failed at network-interface;',
                       'reboot: Restarting system', 'terminating on signal'):
            with self.assertRaises(RuntimeError):
                verify_expiry(0, 15, marker)
