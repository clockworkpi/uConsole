import json
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

from forge_ram_transport import RecoveryProbe
import test_ram_identity
import test_recovery_layout
import base64


class RamTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.key, self.known = root/'key', root/'known'
        for path in (self.key, self.known):
            path.write_text('fixture')
            path.chmod(0o600)
        fixture = test_ram_identity.RamIdentityTests()
        fixture.setUp()
        self.record = fixture.record
        self.args = ('clockworkpi.local', self.key, self.known, fixture.nonce,
                     self.record['kernel'], fixture.serial)

    def test_fixed_probe_uses_only_pinned_recovery_authentication(self):
        probe = RecoveryProbe(*self.args)
        reply = subprocess.CompletedProcess([], 0, json.dumps(self.record), '')
        with patch('forge_ram_transport.subprocess.run', return_value=reply) as run:
            result = probe.inspect(expected_boot_id=self.record['boot_id'])
        argv = run.call_args.args[0]
        for setting in ('StrictHostKeyChecking=yes', 'IdentityAgent=none', 'ControlPath=none',
                        'ClearAllForwardings=yes', 'GlobalKnownHostsFile=/dev/null'):
            self.assertIn(setting, argv)
        self.assertEqual(argv[1:3], ['-F', '/dev/null'])
        self.assertEqual(argv[-2], 'root@clockworkpi.local')
        self.assertFalse(result['verification']['mutation_authorized'])
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_real_probe_child_cannot_consume_owner_protocol_stdin(self):
        child = 'import json,sys; print(json.dumps(dict(consumed=sys.stdin.read())))'
        source = '''import json,sys
sys.path.insert(0,sys.argv[2])
from unittest.mock import patch
from forge_ram_transport import RecoveryProbe
probe=object.__new__(RecoveryProbe)
with patch.object(RecoveryProbe, '_argv', return_value=[sys.executable,'-c',sys.argv[1]]):
    observed=probe._observe('fixture')
print(json.dumps(dict(observed=observed,remaining=sys.stdin.read())))
'''
        result = subprocess.run([sys.executable, '-c', source, child, str(Path(__file__).resolve().parents[1]/'tools')], input='OWNER_PROTOCOL_CANARY\n',
                                text=True, capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), dict(observed=dict(consumed=''), remaining='OWNER_PROTOCOL_CANARY\n'))

    def test_changed_key_or_trust_file_never_contacts_target(self):
        for path in (self.key, self.known):
            probe = RecoveryProbe(*self.args)
            path.write_text(path.read_text()+'changed')
            with patch('forge_ram_transport.subprocess.run') as run:
                with self.assertRaises(ValueError):
                    probe.inspect()
                run.assert_not_called()

    def test_reboot_and_transport_failure_are_not_retried(self):
        probe = RecoveryProbe(*self.args)
        for reply in (subprocess.CompletedProcess([], 0, json.dumps(self.record), ''),
                      subprocess.CompletedProcess([], 255, '', 'denied')):
            with patch('forge_ram_transport.subprocess.run', return_value=reply) as run:
                with self.assertRaises((ValueError, RuntimeError)):
                    probe.inspect(expected_boot_id='00000000-0000-0000-0000-000000000000')
                run.assert_called_once()

    def test_public_key_file_and_option_hostname_are_rejected(self):
        with self.assertRaises(ValueError):
            RecoveryProbe('-oProxyCommand=bad', *self.args[1:])
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            RecoveryProbe(*self.args)

    def test_storage_read_is_bracketed_by_same_boot_and_never_authorizes_restore(self):
        fixture = test_recovery_layout.RecoveryLayoutTests()
        fixture.setUp()
        layout = dict(fixture.observed,mbr=base64.b64encode(fixture.header).decode())
        probe = RecoveryProbe(*self.args)
        with patch.object(RecoveryProbe, '_observe', side_effect=[self.record,layout,self.record]) as observe:
            result = probe.inspect_storage(fixture.cid, fixture.disk_id, expected_boot_id=self.record['boot_id'])
            self.assertEqual(observe.call_count,3)
            self.assertFalse(result['extent']['restore_authorized'])
        changed = dict(self.record,boot_id='00000000-0000-0000-0000-000000000000')
        with patch.object(RecoveryProbe, '_observe', side_effect=[self.record,layout,changed]):
            with self.assertRaisesRegex(ValueError,'rebooted'):
                probe.inspect_storage(fixture.cid, fixture.disk_id, expected_boot_id=self.record['boot_id'])
        with patch.object(RecoveryProbe, '_observe') as observe:
            with self.assertRaises(ValueError):
                probe.inspect_storage(fixture.cid, fixture.disk_id, expected_boot_id=None)
            observe.assert_not_called()
