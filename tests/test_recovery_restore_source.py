import copy
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from forge_recovery_restore_source import CHUNK_BYTES, prepare, stream, validate, digest


class RestoreSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'backup'
        self.source.mkdir(mode=0o700)
        self.root_bytes=b'r'*CHUNK_BYTES+b'z'*512
        self.data=b'p'*512+self.root_bytes+b's'*512
        payload=gzip.compress(self.data)
        self.expected=dict(offset=512,bytes=len(self.root_bytes),sha256=hashlib.sha256(self.root_bytes).hexdigest())
        self.plan=dict(backup_kind='whole-card-bytes',device='/dev/mmcblk0',
                       source=dict(device='/dev/mmcblk0',length_bytes=len(self.data)),
                       extent=dict(disk_bytes=len(self.data),offset_bytes=512,length_bytes=len(self.root_bytes)))
        self.accepted=dict(status='verified-card-byte-backup',compressed_bytes=len(payload),
                           card=dict(bytes=len(self.data),sha256=hashlib.sha256(self.data).hexdigest()))
        self.save(self.source/'plan.json',self.plan)
        self.save(self.source/'acceptance.json',self.accepted)
        (self.source/'card.img.gz').write_bytes(payload)
        (self.source/'card.img.gz').chmod(0o600)

    def save(self,path,value):
        path.write_text(json.dumps(value))
        path.chmod(0o600)

    def prepared(self):
        result=prepare(self.source,self.root/'manifest',self.expected)
        manifest=json.loads((self.root/'manifest/manifest.json').read_text())
        return result,manifest

    def test_verified_manifest_and_bounded_exact_root_stream(self):
        result,manifest=self.prepared()
        self.assertEqual(result['status'],'verified-root-chunk-source')
        self.assertEqual(result['chunk_count'],2)
        self.assertEqual(result['manifest_sha256'],hashlib.sha256((self.root/'manifest/manifest.json').read_bytes()).hexdigest())
        consumed=[]
        pulses=[]
        sent=stream(self.source,manifest,result['manifest_sha256'],
                    lambda chunk,data: consumed.append((chunk,data)),heartbeat=lambda: pulses.append(True))
        self.assertEqual(b''.join(data for _,data in consumed),self.root_bytes)
        self.assertEqual([chunk for chunk,_ in consumed],manifest['chunks'])
        self.assertTrue(all(len(data)<=CHUNK_BYTES for _,data in consumed))
        self.assertGreaterEqual(len(pulses),4)
        self.assertEqual(sent['status'],'verified-source-stream')
        self.assertFalse(sent['target_restore_verified'])
        self.assertFalse(sent['normal_boot_release_authorized'])
        self.assertFalse(manifest['target_write_authorized'])
        self.assertFalse(manifest['normal_boot_release_authorized'])
        with self.assertRaises(FileExistsError): prepare(self.source,self.root/'manifest',self.expected)

    def test_manifest_tamper_and_noncontiguous_or_loose_chunks_rejected(self):
        result,manifest=self.prepared()
        changed=copy.deepcopy(manifest)
        changed['root']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'pinned digest'): validate(changed,result['manifest_sha256'])
        for field,value in (('offset',1),('offset',False),('bytes',CHUNK_BYTES-512),('sha256','bad')):
            changed=copy.deepcopy(manifest)
            changed['chunks'][0][field]=value
            with self.assertRaises(ValueError): validate(changed,digest(changed))
        for field,value in (('schema',True),('chunk_bytes',True),('target_write_authorized',True),
                            ('normal_boot_release_authorized',True),('chunks',manifest['chunks'][:-1])):
            changed=copy.deepcopy(manifest)
            changed[field]=value
            with self.assertRaises(ValueError): validate(changed,digest(changed))

    def test_wrong_chunk_is_not_delivered_even_under_a_new_pin(self):
        _,manifest=self.prepared()
        manifest['chunks'][0]['sha256']='0'*64
        consumer=Mock()
        with self.assertRaisesRegex(ValueError,'before transmission'):
            stream(self.source,manifest,digest(manifest),consumer)
        consumer.assert_not_called()

    def test_source_identity_change_rejected_before_delivery(self):
        result,manifest=self.prepared()
        path=self.source/'card.img.gz'
        initial=path.stat()
        os.utime(path,ns=(initial.st_atime_ns,initial.st_mtime_ns+1000000))
        consumer=Mock()
        with self.assertRaisesRegex(ValueError,'identity differs'):
            stream(self.source,manifest,result['manifest_sha256'],consumer)
        consumer.assert_not_called()

    def test_consumer_failure_is_not_a_stream_completion(self):
        result,manifest=self.prepared()
        consumer=Mock(side_effect=RuntimeError('transport disconnected'))
        with self.assertRaisesRegex(RuntimeError,'transport disconnected'):
            stream(self.source,manifest,result['manifest_sha256'],consumer)
        self.assertEqual(consumer.call_count,1)

    def test_consumer_cannot_mutate_remaining_manifest(self):
        result,manifest=self.prepared()
        delivered=[]
        def consume(chunk,data):
            manifest['chunks'].clear()
            manifest['root']['sha256']='0'*64
            chunk['sha256']='0'*64
            delivered.append(data)
        sent=stream(self.source,manifest,result['manifest_sha256'],consume)
        self.assertEqual(sent['root'],self.expected)
        self.assertEqual(b''.join(delivered),self.root_bytes)

    def test_changed_source_during_prepare_retains_failure_not_manifest(self):
        def heartbeat():
            path=self.source/'card.img.gz'
            old=path.stat()
            os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns+1000000))
        with self.assertRaisesRegex(ValueError,'changed during streaming'):
            prepare(self.source,self.root/'manifest',self.expected,heartbeat=heartbeat)
        self.assertFalse((self.root/'manifest/manifest.json').exists())
        self.assertEqual(json.loads((self.root/'manifest/acceptance.json').read_text())['status'],'incomplete')

    def test_changed_source_during_delivery_never_returns_success(self):
        result,manifest=self.prepared()
        delivered=[]
        def consume(chunk,data):
            delivered.append(chunk)
            self.save(self.source/'acceptance.json',dict(self.accepted,changed=True))
        with self.assertRaisesRegex(ValueError,'changed during streaming'):
            stream(self.source,manifest,result['manifest_sha256'],consume)
        self.assertEqual(len(delivered),2)

    def test_wrong_independent_root_or_card_digest_retains_failure(self):
        for which in ('root','card'):
            with self.subTest(which=which):
                expected=dict(self.expected)
                if which=='root': expected['sha256']='0'*64
                else:
                    accepted=copy.deepcopy(self.accepted)
                    accepted['card']['sha256']='0'*64
                    self.save(self.source/'acceptance.json',accepted)
                out=self.root/which
                with self.assertRaisesRegex(ValueError,'checksum differs'): prepare(self.source,out,expected)
                self.assertFalse((out/'manifest.json').exists())
                self.assertEqual(json.loads((out/'acceptance.json').read_text())['status'],'incomplete')

    def test_bad_geometry_or_scope_rejected_without_output(self):
        for field,value in (('backup_kind','root-partition-bytes'),
                            ('extent',dict(disk_bytes=len(self.data),offset_bytes=513,length_bytes=len(self.root_bytes)))):
            changed=dict(self.plan)
            changed[field]=value
            self.save(self.source/'plan.json',changed)
            with self.assertRaises(ValueError): prepare(self.source,self.root/'bad',self.expected)
            self.assertFalse((self.root/'bad').exists())

    def test_private_source_required(self):
        path=self.source/'card.img.gz'
        path.chmod(0o644)
        with self.assertRaises(PermissionError): prepare(self.source,self.root/'manifest',self.expected)
        self.assertFalse((self.root/'manifest').exists())


if __name__=='__main__': unittest.main()
