from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import validate_target_service_author_gui as validation


class ServiceAuthorGUIGuards(unittest.TestCase):
    def test_invalid_host_rejected_before_artifacts_or_ssh(self):
        with tempfile.TemporaryDirectory() as root, patch.object(validation, 'rpc') as rpc:
            output = Path(root) / 'proof'
            with self.assertRaises(ValueError):
                validation.run('-oProxyCommand=bad', output)
            rpc.assert_not_called()
            self.assertFalse(output.exists())

    def test_existing_evidence_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root, patch.object(validation, 'rpc') as rpc:
            with self.assertRaises(FileExistsError):
                validation.run('target', root)
            rpc.assert_not_called()

    def test_existing_unit_refused_before_gui_or_target_writes(self):
        def observed(host, code, request):
            unit = request['scope']['services'][0]
            return {'nonce': request['nonce'], 'identity': {},
                    'state': {'services': {unit: {'LoadState': 'loaded'}}}}
        with tempfile.TemporaryDirectory() as root, patch.object(validation, 'rpc', side_effect=observed), \
                patch.object(validation, 'GUIServiceAuthorTransitions') as gui:
            output = Path(root) / 'proof'
            with self.assertRaisesRegex(ValueError, 'must be absent'):
                validation.run('target', output)
            gui.assert_not_called()
            self.assertTrue((output / 'incomplete.json').exists())
