import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_protocol import Sender, receive, MAX_HEADER
from forge_recovery_restore_source import stream
import test_recovery_restore_source


class RestoreProtocolTests(unittest.TestCase):
    CHILD = '''import json,os,sys,stat
from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_protocol import receive
path,manifest_path,pin,binding=sys.argv[1:]
manifest=json.load(open(manifest_path))
fd=os.open(path,os.O_RDWR|os.O_NOFOLLOW)
try:
    info=os.fstat(fd)
    assert stat.S_ISREG(info.st_mode) and info.st_size==manifest['root']['bytes']
    writer=RootChunkWriter(fd,manifest,pin)
    def ack(value): print(json.dumps(value),flush=True)
    result=receive(sys.stdin.buffer,writer,json.loads(binding),ack)
    print(json.dumps(result),flush=True)
finally: os.close(fd)
'''

    def setUp(self):
        fixture=test_recovery_restore_source.RestoreSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture=fixture
        result,self.manifest=fixture.prepared()
        self.pin=result['manifest_sha256']
        self.binding=dict(plan_sha256='a'*64,manifest_sha256=self.pin,attempt='b'*32,
                          boot_id='01234567-1234-1234-1234-0123456789ab')
        self.path=fixture.root/'disposable-root.img'
        self.path.write_bytes(b'0'*len(fixture.root_bytes))
        self.fd=os.open(self.path,os.O_RDWR)
        self.addCleanup(os.close,self.fd)
        self.writer=RootChunkWriter(self.fd,self.manifest,self.pin)
        self.output=io.BytesIO()
        self.sender=Sender(self.output,self.manifest,self.pin,self.binding)

    def wire(self):
        receipt=stream(self.fixture.source,self.manifest,self.pin,self.sender.chunk)
        self.sender.finish(receipt)
        return self.output.getvalue()

    def process(self,wire):
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'tools'))
        return subprocess.run([sys.executable,'-I','-c',
            'import sys; sys.path.insert(0,'+repr(env['PYTHONPATH'])+');\n'+self.CHILD,
            str(self.path),str(self.fixture.root/'manifest/manifest.json'),self.pin,json.dumps(self.binding)],
            input=wire,capture_output=True,timeout=20,env=env)

    def test_real_process_pipes_complete_with_ordered_acknowledgements(self):
        result=self.process(self.wire())
        self.assertEqual(result.returncode,0,result.stderr.decode())
        replies=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item['type'] for item in replies],['chunk-verified','chunk-verified','root-verified'])
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes)

    def test_real_process_pipe_disconnect_retains_partial_root_without_completion(self):
        wire=self.wire()
        first_end=wire.index(b'\n')+1+self.manifest['chunks'][0]['bytes']
        result=self.process(wire[:first_end])
        self.assertNotEqual(result.returncode,0)
        replies=[json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([item['type'] for item in replies],['chunk-verified'])
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes[:-512]+b'0'*512)

    def test_archive_to_framed_stream_to_verified_disposable_root(self):
        wire=self.wire()
        acks=[]
        result=receive(io.BytesIO(wire),self.writer,self.binding,acks.append)
        self.assertEqual(result['type'],'root-verified')
        self.assertEqual(len(acks),2)
        self.assertEqual([item['index'] for item in acks],[0,1])
        self.assertEqual(result['result']['root'],self.fixture.expected)
        self.assertFalse(result['result']['normal_boot_release_authorized'])
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes)
        with self.assertRaises(RuntimeError): self.sender.finish({})

    def test_wrong_attempt_boot_or_order_rejected_before_writes(self):
        wire=self.wire()
        first,body=wire.split(b'\n',1)
        for key,value in (('attempt','c'*32),('boot_id','11234567-1234-1234-1234-0123456789ab'),
                          ('plan_sha256','d'*64),('index',True)):
            changed=json.loads(first)
            changed[key]=value
            writer=RootChunkWriter(self.fd,self.manifest,self.pin)
            with self.assertRaises(ValueError):
                receive(io.BytesIO(json.dumps(changed).encode()+b'\n'+body),writer,self.binding,Mock())
            self.assertTrue(writer.failed)
        self.assertEqual(self.path.read_bytes(),b'0'*len(self.fixture.root_bytes))

    def test_oversized_duplicate_or_truncated_header_rejected(self):
        for wire in (b'x'*(MAX_HEADER+1),b'{"type":"chunk","type":"chunk"}\n',b'{'):
            writer=RootChunkWriter(self.fd,self.manifest,self.pin)
            with self.assertRaises(ValueError): receive(io.BytesIO(wire),writer,self.binding,Mock())
        self.assertEqual(self.path.read_bytes(),b'0'*len(self.fixture.root_bytes))

    def test_partial_second_chunk_preserves_first_write_but_never_finishes(self):
        wire=self.wire()
        first_end=wire.index(b'\n')+1+self.manifest['chunks'][0]['bytes']
        second_body=wire.index(b'\n',first_end)+1
        acks=[]
        with self.assertRaisesRegex(ValueError,'body ended early'):
            receive(io.BytesIO(wire[:second_body+100]),self.writer,self.binding,acks.append)
        self.assertEqual(len(acks),1)
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes[:-512]+b'0'*512)
        self.assertTrue(self.writer.failed)
        with self.assertRaises(RuntimeError): self.writer.finish()

    def test_missing_completion_or_extra_bytes_never_returns_root_success(self):
        wire=self.wire()
        final=0
        for chunk in self.manifest['chunks']:
            final=wire.index(b'\n',final)+1+chunk['bytes']
        for broken in (wire[:final],wire+b'!'):
            writer=RootChunkWriter(self.fd,self.manifest,self.pin)
            with self.assertRaises(ValueError): receive(io.BytesIO(broken),writer,self.binding,Mock())
            self.assertTrue(writer.failed)
            self.assertFalse(writer.finished)

    def test_lost_chunk_acknowledgement_does_not_retry_write(self):
        with self.assertRaisesRegex(RuntimeError,'lost acknowledgement'):
            receive(io.BytesIO(self.wire()),self.writer,self.binding,
                    Mock(side_effect=RuntimeError('lost acknowledgement')))
        self.assertEqual(self.writer.index,1)
        self.assertTrue(self.writer.failed)
        with self.assertRaises(RuntimeError): self.writer.finish()

    def test_sender_rejects_unverified_data_before_output(self):
        with self.assertRaises(ValueError): self.sender.chunk(self.manifest['chunks'][0],b'wrong')
        self.assertEqual(self.output.getvalue(),b'')
        with self.assertRaises(RuntimeError): self.sender.chunk({},b'')

    def test_sender_completes_short_transport_writes_and_rejects_no_progress(self):
        class Short(io.BytesIO):
            def write(self,data): return super().write(data[:10000])
        output=Short()
        sender=Sender(output,self.manifest,self.pin,self.binding)
        receipt=stream(self.fixture.source,self.manifest,self.pin,sender.chunk)
        sender.finish(receipt)
        result=receive(io.BytesIO(output.getvalue()),self.writer,self.binding,Mock())
        self.assertEqual(result['type'],'root-verified')
        stalled=Sender(Mock(write=Mock(return_value=0)),self.manifest,self.pin,self.binding)
        with self.assertRaises(OSError): stalled.chunk(self.manifest['chunks'][0],self.fixture.root_bytes[:-512])
        self.assertTrue(stalled.terminal)

    def test_source_completion_must_not_claim_target_restore_or_release(self):
        receipt=stream(self.fixture.source,self.manifest,self.pin,self.sender.chunk)
        receipt['target_restore_verified']=True
        with self.assertRaises(ValueError): self.sender.finish(receipt)


if __name__=='__main__': unittest.main()
