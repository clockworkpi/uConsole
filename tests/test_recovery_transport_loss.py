import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from validate_recovery_transport_loss import WORKER


@unittest.skipUnless(sys.platform == 'linux', 'Target worker checks Linux machine identity')
class TransportLossWorkerTests(unittest.TestCase):
    def test_fixture_mapping_receipt_recovery_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'ledger').mkdir(mode=0o700)
            source = root / 'source.img'
            source.write_bytes(b'disposable fixture')
            source.chmod(0o600)
            token = 'a' * 32
            plan = dict(schema=2, kind='private-recovery-image', host='unused.local',
                        machine_id=Path('/etc/machine-id').read_text().strip(),
                        fstab_sha256='b' * 64, boot_source='/dev/mmcblk0p1', source=str(source),
                        destination='/boot/firmware/forge-recovery-' + token + '.img',
                        sha256=hashlib.sha256(source.read_bytes()).hexdigest(), size=source.stat().st_size,
                        stage_token=token, preimage={'kind': 'absent'})
            tools = Path(__file__).resolve().parents[1] / 'tools'
            names = ('forge_target_backup', 'forge_target_files', 'forge_target_journal',
                     'forge_recovery_image', 'forge_recovery_journal', 'forge_recovery_ledger')
            payload = dict(plan=plan, fixture=str(root), direction='apply', nonce='c' * 32,
                           digest='d' * 64, drop=True,
                           modules=[(n, (tools / (n + '.py')).read_text()) for n in names])
            def invoke():
                return subprocess.run([sys.executable, '-c', WORKER], input=json.dumps(payload) + '\n',
                                      text=True, capture_output=True, check=True, timeout=20).stdout.strip()
            self.assertEqual(invoke(), 'DURABLE-RECEIPT')
            self.assertEqual((root / 'destination.img').read_bytes(), source.read_bytes())
            payload.update(direction='fence-apply', drop=False)
            result = json.loads(invoke())
            self.assertEqual(result['outcome']['status'], 'completed')
            self.assertEqual(result['outcome']['result']['sha256'], plan['sha256'])
            payload.update(direction='restore', nonce='e' * 32)
            self.assertEqual(json.loads(invoke())['status'], 'removed')
            self.assertFalse((root / 'destination.img').exists())
