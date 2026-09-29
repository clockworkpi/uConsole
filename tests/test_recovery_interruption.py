import json
from pathlib import Path
import sys
import tempfile
import unittest

from validate_recovery_interruption import qualify


@unittest.skipUnless(sys.platform == 'linux', 'Injected write boundary uses Linux procfs')
class RecoveryInterruptionTests(unittest.TestCase):
    def test_process_crashes_preserve_evidence_and_receipt_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'evidence'
            result = qualify(directory)
            self.assertEqual(result, json.loads((directory / 'acceptance.json').read_text()))
            self.assertEqual([r['reconciliation'] for r in result['observations']],
                             ['incomplete', 'incomplete', 'completed'])
            self.assertEqual([r['destination_present'] for r in result['observations']],
                             [False, True, False])
            self.assertEqual([r['scratch_present'] for r in result['observations']],
                             [True, False, False])
            self.assertFalse(result['boot_paths_changed'])
