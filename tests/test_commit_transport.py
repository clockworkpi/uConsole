import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forge_recovery_commit_transport import dispatch, exchange
from forge_recovery_bootcommit import plan_digest
from forge_target_journal import write_record, private_directory
import test_commit_protocol as protocol_fixture
import test_recovery_bootplan


WORKER = r'''
import json,sys
r=json.loads(sys.stdin.buffer.readline())
def emit(value): print(json.dumps(value),flush=True)
if r.get('mode')=='hang': sys.stdin.buffer.read(1); sys.exit(1)
for message in r['before']: emit(message)
approval=json.loads(sys.stdin.buffer.readline())
assert approval['commit']=='pin'
if r.get('mode')=='lost': sys.stderr.write('lost reply fixture\n'); sys.exit(1)
for message in r['after']: emit(message)
'''


class CommitTransportTests(unittest.TestCase):
    def pump(self,mode='success'):
        plan,protocol=protocol_fixture.fixture()
        progress=lambda name:dict(type='progress',attempt='attempt',range=name,
            bytes=plan[name+'_guard']['bytes'],total=plan[name+'_guard']['bytes'])
        request=dict(padding='x'*300000,mode=mode,
            before=[progress('root'),progress('prefix'),protocol_fixture.prepared(protocol)],
            after=[protocol_fixture.unmounted(),progress('root'),protocol_fixture.complete()])
        events,processes=[],[]
        original=subprocess.Popen
        def launch(*args,**kwargs):
            process=original(*args,**kwargs)
            processes.append(process)
            return process
        def heartbeat():
            self.assertTrue(protocol.may_renew)
            events.append('renew')
        def prepared(message):
            self.assertEqual(protocol.phase,'prepared')
            events.append('commit')
            return dict(commit='pin')
        try:
            with tempfile.TemporaryFile() as transcript,tempfile.TemporaryFile() as errors, \
                    patch('forge_recovery_commit_transport.subprocess.Popen',side_effect=launch):
                result=exchange([sys.executable,'-I','-c',WORKER],request,protocol,
                    prepared=prepared,unmounted=lambda _:events.append('unmounted'),heartbeat=heartbeat,
                    transcript=transcript,errors=errors,timeout=0.2 if mode=='hang' else 10)
                transcript.seek(0)
                self.assertIn(b'"type": "complete"',transcript.read())
                self.assertLess(events.index('commit'),events.index('unmounted'))
                return result
        finally:
            self.assertEqual(len(processes),1)
            self.assertIsNotNone(processes[0].poll())

    def test_real_duplex_process_large_stdin_and_ordered_completion(self):
        self.assertEqual(self.pump()['status'],'boot-file-commit-verified')

    def test_lost_acknowledgement_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError,'uncertain'): self.pump('lost')

    def test_timeout_terminates_same_process(self):
        with self.assertRaises(TimeoutError): self.pump('hang')

    def journal_fixture(self,directory):
        fixture=test_recovery_bootplan.RecoveryBootPlanTests()
        fixture.setUp()
        fixture.binding.update(mode='emulated',serial=None)
        fixture.observation['mode']='emulated'
        plan=fixture.compile()
        plan['stage_token']='a'*32
        fd=private_directory(directory)
        try: pin=write_record(fd,'plan.json',plan)
        finally: os.close(fd)
        binding=plan['binding']
        probe=SimpleNamespace(**{key:binding[key] for key in ('nonce','kernel','serial','mode')})
        probe.inspect_storage=lambda *args,**kwargs:dict(extent=binding['extent'])
        probe.inspect=lambda **kwargs:None
        probe._argv=lambda command:['never-start-this',command]
        lease=SimpleNamespace(probe=probe,boot_id=binding['boot_id'],owner='b'*64,renew=lambda:dict(sequence=1))
        return plan,pin,probe,lease

    def test_durable_intent_precedes_send_and_second_attempt_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            plan,pin,probe,lease=self.journal_fixture(directory)
            def transfer(argv,request,protocol,**options):
                reply=options['prepared'](dict(type='prepared',attempt=request['attempt'],pin=pin,binding=plan['binding']))
                intent=json.loads((directory/'commit-attempt/commit-intent.json').read_text())
                self.assertEqual(intent,reply)
                options['unmounted'](dict(type='unmounted'))
                return dict(status='fixture-only')
            approve=lambda plan:dict(commit=pin,boot_id=plan['binding']['boot_id'])
            with patch('forge_recovery_commit_transport.exchange',side_effect=transfer) as transfer_mock:
                result=dispatch(probe,directory,pin,lease,authorize=approve)
                self.assertEqual(result['status'],'acknowledged')
                with self.assertRaises(FileExistsError): dispatch(probe,directory,pin,lease,authorize=approve)
                self.assertEqual(transfer_mock.call_count,1)
            for path in (directory/'commit-attempt').iterdir():
                self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_owner_refusal_retains_attempt_without_commit_intent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            plan,pin,probe,lease=self.journal_fixture(directory)
            def transfer(argv,request,protocol,**options):
                return options['prepared'](dict(type='prepared'))
            with patch('forge_recovery_commit_transport.exchange',side_effect=transfer):
                with self.assertRaisesRegex(ValueError,'Owner refused'):
                    dispatch(probe,directory,pin,lease,authorize=lambda plan:{})
            attempt=directory/'commit-attempt'
            self.assertFalse((attempt/'commit-intent.json').exists())
            self.assertEqual(json.loads((attempt/'acceptance.json').read_text())['status'],'uncertain')


if __name__=='__main__': unittest.main()
