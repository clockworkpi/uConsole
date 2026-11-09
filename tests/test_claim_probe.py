import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from forge_claim_probe import SOURCE
import test_ram_identity


class ClaimProbeTests(unittest.TestCase):
    def test_physical_boot_rejected_before_open_or_nested_worker(self):
        fixture = test_ram_identity.RamIdentityTests()
        fixture.setUp()
        directory = Path(__file__).resolve().parents[1]/'tools'
        modules = [(name, (directory/(name+'.py')).read_text()) for name in
                   ('forge_ram_identity', 'forge_recovery_layout', 'forge_recovery_claim')]
        name, source = modules[0]
        modules[0] = (name, source+'\nREADER='+repr('print('+repr(json.dumps(fixture.record))+')'))
        request = dict(modules=modules, binding=dict(nonce='a'*32, kernel='6.12.62-v8+',
            boot_id='12345678-1234-1234-1234-123456789abc', cid='a'*32,
            disk_id='f047e001', extent=dict(device='/dev/mmcblk1p2',
                offset_bytes=512, length_bytes=512, disk_bytes=1024)),
            guards={}, boot_script='raise AssertionError("Nested worker must not execute")')
        with patch.dict(sys.modules), patch('os.open') as opened:
            with self.assertRaises(ValueError):
                exec(SOURCE, {'request': request})
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
