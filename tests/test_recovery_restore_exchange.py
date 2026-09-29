import json
from pathlib import Path
import sys
import tempfile
import unittest

from forge_recovery_bootplan import digest
from forge_recovery_restore_exchange import exchange
from forge_recovery_restore_process import run as run_process
from forge_recovery_restore_source import stream
import test_recovery_restore_plan


CHILD = '''import json,os,sys
from unittest.mock import patch
from forge_recovery_restore_io import PipeIO
from forge_recovery_restore_worker import Control
from forge_recovery_restore_chunks import RootChunkWriter
from forge_recovery_restore_protocol import receive,write_header
from forge_recovery_operation_contract import source_kind
request=json.load(open(sys.argv[1]))
plan,pin,manifest,attempt,root,mode=(request[key] for key in ('plan','pin','manifest','attempt','root','mode'))
wire=dict(plan_sha256=pin,manifest_sha256=plan['source_manifest_sha256'],attempt=attempt,boot_id=plan['binding']['boot_id'])
fd=os.open(root,os.O_RDWR)
try:
    with PipeIO(sys.stdin.fileno(),sys.stdout.fileno(),timeout=20,idle_timeout=3) as channel:
        control=Control(channel,plan['binding'],wire)
        with patch('forge_recovery_restore_worker.budget',return_value=240):
            for phase in ('acquire','inspect'): control.approve(phase,pin,plan['binding'])
            control.unmounted()
            control.approve('write',pin,plan['binding'])
            class Writer(RootChunkWriter):
                def apply(self,chunk,data):
                    control.exchange('renew','verification')
                    return super().apply(chunk,data)
            selected=source_kind(plan)
            writer=Writer(fd,manifest,wire['manifest_sha256'],expected_kind=selected)
            def ack(value):
                if mode=='disconnect': raise OSError('intentional lost chunk acknowledgement')
                control.acknowledge(value)
            def progress(done,total):
                control.last_renewal-=61
                control.progress('restored-root',done,total)
            result=receive(channel,writer,wire,ack,progress=progress,defer_eof=True,expected_kind=selected)['result']
            control.finish_input()
            if channel.read(1)!=b'': raise ValueError('Expected input half-close')
            # Fixture-only receipt: hardware guards are tested separately.
            receipt=dict(status='root-restore-verified',plan_sha256=pin,
                source_manifest_sha256=wire['manifest_sha256'],boot_id=wire['boot_id'],
                root=plan['root_after'],prefix=plan['prefix_guard'],suffix=plan['suffix_guard'],
                bytes_written=result['bytes_written'],protected_ranges_verified=True,boot_unmounted=True,
                normal_boot_release_authorized=False,physical_restore_qualified=False)
            write_header(channel,dict(type='complete',**wire,result=receipt,historical_replay=False))
            if mode=='trailing': channel.write(b'!')
finally: os.close(fd)
if mode=='exit-failure': raise SystemExit(7)
'''


class RestoreExchangeTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.plan = fixture.compile()
        self.pin = digest(self.plan)
        self.attempt = 'e'*32
        self.root = fixture.source.root/'duplex-root.img'
        self.before = b'0'*len(fixture.source.root_bytes)
        self.root.write_bytes(self.before)
        self.events = []
        self.sequence = 0

    def renew(self):
        self.sequence += 1
        binding = self.plan['binding']
        return dict(schema=1,purpose='offline-backup',nonce=binding['nonce'],boot_id=binding['boot_id'],
            owner=binding['lease_owner'],sequence=self.sequence,deadline_monotonic=300,
            hard_deadline_monotonic=86400,root_write_authorized=False,normal_boot_release_authorized=False)

    def authorize(self, plan, pin, phase):
        return dict(restore=pin,boot_id=plan['binding']['boot_id'],phase=phase)

    def produce(self, consume):
        return stream(self.fixture.source.source,self.fixture.manifest,self.fixture.source_pin,consume)

    def record(self, kind, value):
        self.events.append((kind,value))

    def run_exchange(self, mode='normal', **hooks):
        request = self.fixture.source.root/'duplex-request.json'
        request.write_text(json.dumps(dict(plan=self.plan,pin=self.pin,manifest=self.fixture.manifest,
                                          attempt=self.attempt,root=str(self.root),mode=mode)))
        tools = str(Path(__file__).resolve().parents[1]/'tools')
        command = [sys.executable,'-I','-c','import sys;sys.path.insert(0,'+repr(tools)+');\n'+CHILD,str(request)]
        def operation(channel, half_close, wait_success):
            return exchange(channel,self.plan,self.pin,self.fixture.manifest,self.attempt,
                hooks.get('produce',self.produce),authorize=hooks.get('authorize',self.authorize),
                renew=self.renew,record=hooks.get('record',self.record),
                half_close=half_close,wait_success=wait_success)
        with tempfile.TemporaryFile() as errors:
            return run_process(command,operation,errors,timeout=20,idle_timeout=3)

    def test_real_duplex_child_restores_bytes_while_renewing_between_chunks_and_readback(self):
        result = self.run_exchange()
        self.assertEqual(self.root.read_bytes(),self.fixture.source.root_bytes)
        self.assertEqual(result['root'],self.plan['root_after'])
        self.assertFalse(result['physical_restore_qualified'])
        kinds = [kind for kind,_ in self.events]
        self.assertEqual(kinds[-1],'exchange-complete')
        self.assertEqual(kinds.count('chunk-intent'),2)
        self.assertGreater(self.sequence,5)
        self.assertLess(kinds.index('source-verified'),kinds.index('input-close-intent'))

    def test_denied_write_approval_never_streams_source(self):
        def authorize(plan,pin,phase):
            return {} if phase=='write' else self.authorize(plan,pin,phase)
        with self.assertRaisesRegex(ValueError,'Owner refused'):
            self.run_exchange(authorize=authorize)
        self.assertEqual(self.root.read_bytes(),self.before)
        self.assertNotIn('chunk-intent',[kind for kind,_ in self.events])

    def test_lost_ack_keeps_partial_root_and_no_host_completion(self):
        with self.assertRaises(ValueError): self.run_exchange('disconnect')
        self.assertEqual(self.root.read_bytes(),self.fixture.source.root_bytes[:-512]+b'0'*512)
        self.assertNotIn('exchange-complete',[kind for kind,_ in self.events])

    def test_trailing_output_invalidates_even_correct_root_receipt(self):
        with self.assertRaisesRegex(ValueError,'after restore worker completion'):
            self.run_exchange('trailing')
        self.assertNotIn('exchange-complete',[kind for kind,_ in self.events])

    def test_nonzero_process_exit_invalidates_correct_root_receipt(self):
        with self.assertRaisesRegex(RuntimeError,'exited unsuccessfully'):
            self.run_exchange('exit-failure')
        self.assertNotIn('exchange-complete',[kind for kind,_ in self.events])

    def test_journal_failure_prevents_write_approval_delivery(self):
        def record(kind,value):
            if kind=='control-response-intent' and value['phase']=='write':
                raise OSError('Journal full')
            self.record(kind,value)
        with self.assertRaisesRegex(OSError,'Journal full'): self.run_exchange(record=record)
        self.assertEqual(self.root.read_bytes(),self.before)

    def test_unverified_source_bytes_never_reach_child(self):
        def produce(consume):
            consume(self.fixture.manifest['chunks'][0],b'bad source')
        with self.assertRaisesRegex(ValueError,'Unverified restore chunk'):
            self.run_exchange(produce=produce)
        self.assertEqual(self.root.read_bytes(),self.before)

    def test_archive_failure_after_last_chunk_never_sends_source_completion(self):
        def produce(consume):
            self.produce(consume)
            raise ValueError('Archive final verification failed')
        with self.assertRaisesRegex(ValueError,'Archive final verification'):
            self.run_exchange(produce=produce)
        self.assertEqual(self.root.read_bytes(),self.fixture.source.root_bytes)
        kinds = [kind for kind,_ in self.events]
        self.assertNotIn('source-verified',kinds)
        self.assertNotIn('exchange-complete',kinds)


if __name__=='__main__': unittest.main()
