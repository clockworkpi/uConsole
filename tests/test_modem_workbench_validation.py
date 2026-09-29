from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import validate_modem_workbench as validation


class ModemWorkbenchGuards(unittest.TestCase):
    def test_cycle_validation_precedes_guest_mutation(self):
        with patch.object(validation, 'fixture') as fixture:
            for count in (0, 21, True, '2'):
                with self.assertRaisesRegex(ValueError, 'Hotplug cycles'):
                    validation.run(None, None, None, hotplug_cycles=count)
            with self.assertRaisesRegex(ValueError, 'require USB hotplug'):
                validation.run(None, None, None, hotplug_cycles=2)
            fixture.assert_not_called()

    def test_traffic_oracle_requires_completion_and_initial_reply(self):
        from modem_hotplug_traffic import PROBE, validate_result
        compile(PROBE, '<hotplug-traffic-guest>', 'exec')
        good = dict(status='completed', initial_reply=True, returncode=0,
                    stdout='100 packets transmitted, 1 received')
        validate_result(good)
        validate_result(dict(good, returncode=1))
        for field, value in [('status', 'failed'), ('initial_reply', False),
                             ('returncode', 2), ('returncode', True), ('stdout', ''),
                             ('stdout', '1 packets transmitted, 1 received'),
                             ('stdout', '100 packets transmitted, 0 received'),
                             ('stdout', '100 packets transmitted, 101 received')]:
            with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
                validate_result(dict(good, **{field: value}))

    def test_hash_guard_precedes_overlay_and_gui_creation(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(validation, 'sha256', return_value='wrong'), \
                patch.object(validation, 'fixture') as fixture:
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                validation.run(Path(directory)/'base.img', 'expected', Path(directory)/'output')
            fixture.assert_not_called()

    def test_probe_uses_same_open_descriptor_for_raw_mode(self):
        script = validation.query_script(3)
        self.assertIn('tty.setraw(fd)', script)
        self.assertIn('os.close(fd)', script)
        self.assertIn('CEREG: 0,3', script)
        compile(script.split('\n', 1)[1].rsplit('\nPY', 1)[0], '<guest-probe>', 'exec')

    def test_reset_probe_is_guest_script_with_exact_usb_identity_guard(self):
        from modem_usb_reset_probe import PROBE
        compile(PROBE, '<usb-reset-guest>', 'exec')
        self.assertIn("== '1e0e'", PROBE)
        self.assertIn("== '9001'", PROBE)
        self.assertIn('len(devices) != 1', PROBE)
        self.assertIn('Refusing reset of a non-Forge USB modem', PROBE)
