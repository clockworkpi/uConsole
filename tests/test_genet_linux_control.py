import copy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / 'tools/test_emulator_genet_linux.py'
spec = importlib.util.spec_from_file_location('genet_linux_control', PATH)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)
SIZE = 1 << 20
SHA = 'a' * 64


class NativeNetworkAcceptance(unittest.TestCase):
    def setUp(self):
        self.report = dict(
            driver='bcmgenet', kernel='6.12.62-v8+', carrier='1', phy_id='0x600d84a2',
            online='0-3', meminfo='MemTotal: 8008476 kB', config=['CONFIG_BCMGENET=y'],
            ip=json.dumps([{'addr_info': [{'family': 'inet', 'local': '10.0.2.15'}]}]),
            interrupts=' 26: 160 0 0 0 GICv2 189 Level eth0\n 27: 21 0 0 0 GICv2 190 Level eth0\n',
            uploaded_sha256=SHA,
            stats=dict(rx_packets=1454, tx_packets=785, rx_bytes=1132996,
                       tx_bytes=1092073, rx_errors=0, tx_errors=0))

    def checks(self, report=None):
        return control.assess_guest(report or self.report, 'raspi4b-8g', '6.12.62-v8+', SHA, SIZE)[0]

    def test_actual_eth0_irq_labels_are_accepted(self):
        self.assertTrue(all(self.checks().values()))

    def test_another_nic_cannot_substitute_for_genet(self):
        self.report['driver'] = 'virtio_net'
        self.assertFalse(self.checks()['native_driver'])

    def test_other_device_interrupts_cannot_hide_missing_nic_irqs(self):
        for interrupts in (' 1: 2000 0 0 0 GICv2 4 Level timer\n',
                           ' 26: 0 0 0 0 GICv2 189 Level eth0\n'):
            self.report['interrupts'] = interrupts
            self.assertFalse(self.checks()['native_interrupts'])

    def test_small_guest_cannot_pass_full_memory_network_claim(self):
        for amount in (1902264, 3000000):
            self.report['meminfo'] = f'MemTotal: {amount} kB'
            self.assertFalse(self.checks()['guest_memory'])

    def test_completed_transfer_does_not_hide_packet_errors_or_missing_wrap(self):
        for counter in ('rx_errors', 'tx_errors'):
            report = copy.deepcopy(self.report)
            report['stats'][counter] = 1
            self.assertFalse(self.checks(report)['error_counters_clean'])
        self.report['stats']['rx_packets'] = 256
        self.assertFalse(self.checks()['rx_over_one_ring'])

    def test_wrong_payload_and_address_fail_independently(self):
        self.report['uploaded_sha256'] = 'b' * 64
        self.report['ip'] = json.dumps([{'addr_info': [{'family': 'inet', 'local': '169.254.1.1'}]}])
        checks = self.checks()
        self.assertFalse(checks['uploaded_payload'])
        self.assertFalse(checks['dhcp_address'])

    def test_generated_guest_programs_compile(self):
        compile(control.server_source('freshnonce', SIZE, SHA), '<guest HTTP server>', 'exec')
        compile(control.probe_source('6.12.62-v8+'), '<guest native probe>', 'exec')

    def test_nonregular_input_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new-evidence'
            candidates = [Path(directory)]
            if Path('/dev/null').exists():
                candidates.append(Path('/dev/null'))
            for image in candidates:
                args = SimpleNamespace(output=output, image=image,
                                       kernel=PATH, dtb=PATH, qemu=PATH, qemu_img=PATH)
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    control.run(args)
                self.assertFalse(output.exists())

    def test_observable_wait_uses_the_private_output_scope(self):
        path = Path('/private-output/serial.sock')
        connector = Mock(return_value='connected socket')
        with patch.object(control.shutil, 'which', return_value='/bin/toolscheme'), \
             patch.object(control.subprocess, 'run') as wait, \
             patch.dict('sys.modules', {'test_emulator_pmic': SimpleNamespace(connect=connector)}):
            self.assertEqual(control.console_connection(path, 'guest'), 'connected socket')
        self.assertEqual(wait.call_args.kwargs['cwd'], path.parent)
        connector.assert_called_once_with(path, 'guest')


if __name__ == '__main__':
    unittest.main()
