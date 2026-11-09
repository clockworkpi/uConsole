import copy
import os
from unittest.mock import patch
import unittest

from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_source import stream
import test_recovery_restore_source


class RestoreChunkTests(unittest.TestCase):
    def setUp(self):
        fixture=test_recovery_restore_source.RestoreSourceTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture=fixture
        self.prepared,self.manifest=fixture.prepared()
        self.pin=self.prepared['manifest_sha256']
        self.path=fixture.root/'disposable-root.img'
        self.path.write_bytes(b'0'*len(fixture.root_bytes)+b'guarded-suffix')
        self.fd=os.open(self.path,os.O_RDWR)
        self.addCleanup(os.close,self.fd)
        self.writer=RootChunkWriter(self.fd,self.manifest,self.pin)
        self.first=self.manifest['chunks'][0]
        self.data=fixture.root_bytes[:self.first['bytes']]

    def send(self,writer=None):
        writer=writer or self.writer
        return stream(self.fixture.source,self.manifest,self.pin,writer.apply)

    def test_real_stream_writes_only_root_range_and_verifies_final_digest(self):
        sent=self.send()
        progress=[]
        result=self.writer.finish(progress=lambda done,total: progress.append((done,total)))
        self.assertEqual(progress[-1],(len(self.fixture.root_bytes),len(self.fixture.root_bytes)))
        self.assertEqual(sorted(set(progress)),progress)
        self.assertEqual(sent['status'],'verified-source-stream')
        self.assertEqual(result['status'],'verified-root-bytes')
        self.assertEqual(result['bytes_written'],len(self.fixture.root_bytes))
        self.assertEqual(result['root'],self.fixture.expected)
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes+b'guarded-suffix')
        for field in ('normal_boot_release_authorized','protected_ranges_verified','physical_restore_qualified'):
            self.assertFalse(result[field])
        with self.assertRaises(RuntimeError): self.writer.finish()
        with self.assertRaises(RuntimeError): self.writer.apply(self.first,self.data)

    def test_matching_bytes_are_synchronized_without_rewrite(self):
        os.pwrite(self.fd,self.fixture.root_bytes,0)
        with patch('forge_recovery_restore_chunks.os.pwrite') as write, \
             patch('forge_recovery_restore_chunks.os.fsync',wraps=os.fsync) as sync:
            self.send()
            result=self.writer.finish()
            write.assert_not_called()
            self.assertEqual(sync.call_count,3)
        self.assertEqual(result['bytes_written'],0)
        self.assertEqual(result['matching_chunks'],2)

    def test_bad_data_and_wrong_chunk_fail_before_any_device_access(self):
        bad=copy.deepcopy(self.first)
        bad['offset']=False
        cases=[(self.first,b'bad'),(bad,self.data),
               (self.manifest['chunks'][1],self.fixture.root_bytes[-512:]),(self.first,bytearray(self.data))]
        for chunk,data in cases:
            writer=RootChunkWriter(self.fd,self.manifest,self.pin)
            with patch('forge_recovery_restore_chunks.range_digest') as read, \
                 patch('forge_recovery_restore_chunks.os.pwrite') as write:
                with self.assertRaises(ValueError): writer.apply(chunk,data)
                read.assert_not_called()
                write.assert_not_called()
            with self.assertRaises(RuntimeError): writer.apply(self.first,self.data)

    def test_short_writes_are_completed_before_ack(self):
        original=os.pwrite
        def short(fd,data,offset): return original(fd,data[:65536],offset)
        with patch('forge_recovery_restore_chunks.os.pwrite',side_effect=short) as write:
            result=self.writer.apply(self.first,self.data)
            self.assertGreater(write.call_count,1)
        self.assertEqual(result['bytes_written'],len(self.data))
        self.assertEqual(os.pread(self.fd,len(self.data),0),self.data)

    def test_partial_write_failure_is_terminal_without_rollback(self):
        original=os.pwrite
        calls=0
        def interrupted(fd,data,offset):
            nonlocal calls
            calls+=1
            if calls==1: return original(fd,data[:512],offset)
            raise OSError('simulated I/O loss')
        with patch('forge_recovery_restore_chunks.os.pwrite',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'I/O loss'): self.writer.apply(self.first,self.data)
        self.assertEqual(os.pread(self.fd,1024,0),self.data[:512]+b'0'*512)
        with self.assertRaises(RuntimeError): self.writer.apply(self.first,self.data)
        with self.assertRaises(RuntimeError): self.writer.finish()

    def test_zero_write_and_sync_failure_are_terminal(self):
        for function,value in (('pwrite',0),('fsync',OSError('sync failed'))):
            writer=RootChunkWriter(self.fd,self.manifest,self.pin)
            option=dict(side_effect=value) if isinstance(value,Exception) else dict(return_value=value)
            with patch('forge_recovery_restore_chunks.os.'+function,**option):
                with self.assertRaises(OSError): writer.apply(self.first,self.data)
            with self.assertRaises(RuntimeError): writer.finish()

    def test_readback_failure_never_acknowledges_or_reuses_writer(self):
        with patch('forge_recovery_restore_chunks.range_digest',return_value='0'*64):
            with self.assertRaisesRegex(ValueError,'readback'): self.writer.apply(self.first,self.data)
        with self.assertRaises(RuntimeError): self.writer.finish()

    def test_finish_requires_all_chunks_and_detects_later_corruption(self):
        incomplete=RootChunkWriter(self.fd,self.manifest,self.pin)
        with self.assertRaisesRegex(ValueError,'incomplete'): incomplete.finish()
        self.send()
        os.pwrite(self.fd,b'!',0)
        with self.assertRaisesRegex(ValueError,'Final restored root digest'): self.writer.finish()

    def test_extra_chunk_and_manifest_mutation_cannot_expand_write_bounds(self):
        self.manifest['chunks'][0]['offset']=len(self.fixture.root_bytes)
        self.manifest['root']['bytes']*=2
        # Writer retained its own pinned copy before the owner-facing input changed.
        first=copy.deepcopy(self.first)
        first['offset']=0
        self.writer.apply(first,self.data)
        second=self.writer.manifest['chunks'][1]
        self.writer.apply(second,self.fixture.root_bytes[-512:])
        with self.assertRaisesRegex(ValueError,'extra'): self.writer.apply(second,self.fixture.root_bytes[-512:])
        self.assertEqual(self.path.read_bytes(),self.fixture.root_bytes+b'guarded-suffix')


if __name__=='__main__': unittest.main()
