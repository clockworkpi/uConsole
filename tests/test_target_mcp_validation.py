from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_target_journal import prepare
from target_mcp_validation import MCPTransitions


class RealTargetMCPTests(unittest.TestCase):
    def test_real_owner_lists_service_authorization_without_accepting_client_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            client = MCPTransitions(root, {'kind': 'service', 'journal': str(root / 'unused'),
                                           'authorization_sha256': 'b' * 64}).start()
            try:
                for key in ('host', 'authorization_sha256', 'kind', 'ledger_parent', 'unit'):
                    with self.subTest(key=key), self.assertRaises(ValueError):
                        client.call('target_transition', {'workspace': 'target', 'transaction': 'proof',
                                                         'direction': 'apply', key: 'override'})
                self.assertFalse((root / 'unused').exists())
            finally:
                client.close()

    def test_real_owner_lists_pins_and_rejects_ungranted_tools_and_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = {'schema': 1, 'machine_id': 'a' * 32,
                      'files': [{'path': '/usr/local/bin/unused-test', 'kind': 'absent'}]}
            prepared = prepare(root / 'transaction', 'unreachable.invalid', before, before)
            client = MCPTransitions(root, prepared).start()
            try:
                tools = client.request('tools/list')['tools']
                self.assertIn('target_transition', [tool['name'] for tool in tools])
                with self.assertRaises(RuntimeError):
                    client.call('guest_exec', {'workspace': 'target', 'script': 'true'})
                with self.assertRaises(ValueError):
                    client.call('target_transition', {'workspace': 'target', 'transaction': 'proof',
                                                     'direction': 'apply', 'host': 'override'})
                self.assertEqual(list((root / 'transaction').glob('event-*')), [])
            finally:
                client.close()
            self.assertEqual(client.process.returncode, 0)
            self.assertTrue((root / 'mcp-transcript.jsonl').stat().st_size)
