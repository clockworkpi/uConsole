import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from forge_recovery_derivative import load, stream
from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_protocol import Sender, receive
from forge_recovery_source_contract import DERIVATIVE
import test_recovery_derivative


class DerivativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_recovery_derivative.RecoveryDerivativeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.change(512, b'enhanced')
        result = self.fixture.run_prepare()
        self.pin = result['manifest_sha256']
        self.manifest, _ = load(self.fixture.output, self.pin)
        self.binding = dict(plan_sha256='a'*64, manifest_sha256=self.pin, attempt='b'*32,
                            boot_id='01234567-1234-1234-1234-0123456789ab')
        self.target = self.fixture.fixture.root/'disposable-root.img'
        self.before = self.fixture.fixture.root_bytes
        self.target.write_bytes(self.before)
        self.fd = os.open(self.target, os.O_RDWR)
        self.addCleanup(os.close, self.fd)

    def wire(self):
        output = io.BytesIO()
        sender = Sender(output, self.manifest, self.pin, self.binding, expected_kind=DERIVATIVE)
        receipt = stream(self.fixture.output, self.pin, sender.chunk)
        sender.finish(receipt)
        return output.getvalue()

    def writer(self):
        return RootChunkWriter(self.fd, self.manifest, self.pin, expected_kind=DERIVATIVE)

    def test_explicit_derivative_stream_writes_changed_chunk_only(self):
        acknowledgements = []
        result = receive(io.BytesIO(self.wire()), self.writer(), self.binding,
                         acknowledgements.append, expected_kind=DERIVATIVE)
        self.assertEqual(self.target.read_bytes(), self.fixture.image.read_bytes()[512:-512])
        self.assertEqual([v['result']['bytes_written'] for v in acknowledgements], [4*1024*1024, 0])
        self.assertEqual(result['result']['bytes_written'], 4*1024*1024)
        self.assertFalse(result['result']['protected_ranges_verified'])
        self.assertFalse(result['result']['physical_restore_qualified'])
        self.assertFalse(result['result']['normal_boot_release_authorized'])

    def test_backup_defaults_reject_derivative_before_writing(self):
        with self.assertRaises(ValueError):
            Sender(io.BytesIO(), self.manifest, self.pin, self.binding)
        with self.assertRaises(ValueError):
            RootChunkWriter(self.fd, self.manifest, self.pin)
        with self.assertRaises(ValueError):
            receive(io.BytesIO(self.wire()), self.writer(), self.binding, lambda _: None)
        self.assertEqual(self.target.read_bytes(), self.before)

    def test_wrong_completion_status_never_finishes_written_root(self):
        wire = self.wire().replace(b'"verified-derivative-stream"', b'"verified-source-stream"')
        writer = self.writer()
        with self.assertRaisesRegex(ValueError, 'source completion'):
            receive(io.BytesIO(wire), writer, self.binding, lambda _: None, expected_kind=DERIVATIVE)
        self.assertTrue(writer.failed)
        self.assertFalse(writer.finished)

    def test_truncated_stream_keeps_partial_result_unqualified(self):
        wire = self.wire()
        first_end = wire.index(b'\n')+1+self.manifest['chunks'][0]['bytes']
        writer = self.writer()
        with self.assertRaises(ValueError):
            receive(io.BytesIO(wire[:first_end]), writer, self.binding, lambda _: None,
                    expected_kind=DERIVATIVE)
        self.assertEqual(writer.index, 1)
        self.assertTrue(writer.failed)
        self.assertFalse(writer.finished)

    def test_real_process_pipe_derivative_transfer(self):
        code = '''import json,os,sys
from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_protocol import receive
from forge_recovery_source_contract import DERIVATIVE
target,manifest_path,pin,binding=sys.argv[1:]
with open(manifest_path) as source: manifest=json.load(source)
fd=os.open(target,os.O_RDWR|os.O_NOFOLLOW)
try:
    writer=RootChunkWriter(fd,manifest,pin,expected_kind=DERIVATIVE)
    result=receive(sys.stdin.buffer,writer,json.loads(binding),
        lambda message: print(json.dumps(message),flush=True),expected_kind=DERIVATIVE)
    print(json.dumps(result),flush=True)
finally: os.close(fd)
'''
        tools = str(Path(__file__).resolve().parents[1]/'tools')
        result = subprocess.run([sys.executable, '-I', '-c',
            'import sys; sys.path.insert(0,'+repr(tools)+');\n'+code,
            str(self.target), str(self.fixture.output/'manifest.json'), self.pin, json.dumps(self.binding)],
            input=self.wire(), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item['type'] for item in replies], ['chunk-verified', 'chunk-verified', 'root-verified'])
        self.assertEqual(self.target.read_bytes(), self.fixture.image.read_bytes()[512:-512])
