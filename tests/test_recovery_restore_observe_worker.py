import copy
import hashlib
import io
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from forge_recovery_bootplan import digest
from forge_recovery_restore_bootstrap import OBSERVATION_BOOTSTRAP, framed, observation_payload
from forge_recovery_restore_plan import INPUT_NAMES
import forge_recovery_restore_observe_worker as worker
import forge_recovery_restore_observe_transport as transport
import test_recovery_restore_plan


class ObservationWorkerTests(unittest.TestCase):
    def setUp(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plan = fixture.compile()
        self.request = dict(protocol=1, plan=plan, pin=digest(plan),
            inputs=dict(zip(INPUT_NAMES, fixture.arguments())), attempt='e'*32,
            query='f'*32, action='fence', observed_boot_id=plan['binding']['boot_id'],
            owner=plan['binding']['lease_owner'], accepted=None)

    def test_fence_response_is_bound_to_query(self):
        channel = io.BytesIO()
        with patch.object(worker, 'fence', return_value={'status':'restore-fenced'}) as fence:
            worker.run(self.request, channel)
        fence.assert_called_once_with(self.request['plan'], self.request['pin'], 'e'*32,
                                      observed_boot_id=self.request['observed_boot_id'])
        reply = json.loads(channel.getvalue())
        self.assertEqual(reply['query'], 'f'*32)
        self.assertEqual(reply['action'], 'fence')

    def test_trailing_input_prevents_target_action(self):
        with patch.object(worker, 'fence') as fence, self.assertRaises(ValueError):
            worker.run(self.request, io.BytesIO(b'extra'))
        fence.assert_not_called()

    def test_inspection_uses_retained_hold_and_explicit_lease(self):
        self.request.update(action='inspect', accepted={'fixture':True})
        with patch.object(worker, 'inspect', return_value={}) as inspect:
            worker.run(self.request, io.BytesIO())
        self.assertEqual(inspect.call_args.args[3], self.request['inputs']['hold_plan'])
        self.assertEqual(inspect.call_args.kwargs['accepted'], {'fixture':True})

    def test_invalid_requests_never_reach_target(self):
        mutations = [dict(action='restore'), dict(protocol=True), dict(query='bad'),
                     dict(owner='0'*64), dict(observed_boot_id='bad'), dict(accepted={}),
                     dict(extra=True)]
        for mutation in mutations:
            with self.subTest(mutation=mutation), patch.object(worker,'fence') as fence:
                request = dict(self.request, **mutation)
                with self.assertRaises(ValueError): worker.run(request, io.BytesIO())
                fence.assert_not_called()

    def test_changed_original_evidence_rejected(self):
        request = copy.deepcopy(self.request)
        request['inputs']['ram_boot_observation']['bootloader']['tryboot'] = 1
        with self.assertRaises(ValueError): observation_payload(request)

    def test_real_isolated_closure_imports_and_rejects_invalid_protocol(self):
        data, _ = observation_payload(self.request)
        packet = json.loads(data)
        packet['request']['protocol'] = 2
        data = json.dumps(packet).encode()
        pin = hashlib.sha256(data).hexdigest()
        result = subprocess.run([sys.executable, '-I', '-S', '-c', OBSERVATION_BOOTSTRAP, pin],
            input=framed(data,pin), capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['packet_sha256'], pin)
        self.assertIn(b'Unexpected restore observation request', result.stderr)

    def test_new_boot_allowed_without_changing_original_plan(self):
        old = copy.deepcopy(self.request['plan'])
        self.request['observed_boot_id'] = '99999999-1234-1234-1234-123456789abc'
        self.assertEqual(worker.checked(self.request)['plan'], old)

    def test_real_process_exchange_with_fixture_hardware_boundary(self):
        data, _ = observation_payload(self.request)
        packet = json.loads(data)
        # Only the hardware boundary is synthetic; loader, worker validation,
        # EOF, response binding, pipe transport and process exit are real.
        packet['modules']['forge_recovery_restore_observe'] = (
            "def fence(*args,**kwargs): return {'status':'fixture-fenced'}\n"
            "def inspect(*args,**kwargs): raise AssertionError('not requested')\n")
        data = json.dumps(packet).encode()
        pin = hashlib.sha256(data).hexdigest()
        command = [sys.executable, '-I', '-S', '-c', OBSERVATION_BOOTSTRAP, pin]
        with patch.object(transport,'observation_payload',return_value=(data,pin)), \
                patch.object(transport,'argv',return_value=command):
            self.assertEqual(transport.capture(None,self.request,io.BytesIO()),
                             {'status':'fixture-fenced'})

    def test_transport_requires_bound_reply_eof_and_process_success(self):
        reply = dict(type='restore-observation', query='f'*32, action='fence',
            plan_sha256=self.request['pin'], attempt='e'*32,
            boot_id=self.request['observed_boot_id'], result={'status':'fixture'})
        for change, trailing, fails in (({},b'',False), ({'query':'0'*32},b'',True),
                                        ({},b'extra',True)):
            with self.subTest(change=change,trailing=trailing):
                events = []
                def run(argv, operation, errors, **kwargs):
                    channel = io.BytesIO(json.dumps(dict(reply,**change)).encode()+b'\n'+trailing)
                    return operation(channel, lambda:events.append('closed'),
                                     lambda:events.append('exited'))
                with patch.object(transport,'run',side_effect=run), \
                        patch.object(transport,'argv',return_value=['fixture']), \
                        patch.object(transport,'start'):
                    if fails:
                        retained = []
                        with self.assertRaises(ValueError):
                            transport.capture(None,self.request,io.BytesIO(),
                                record=lambda kind,value:retained.append((kind,value)))
                        self.assertEqual(events,['closed'])
                        self.assertEqual([kind for kind,_ in retained],['prepared','ready','reply'])
                        self.assertEqual(retained[-1][1],dict(reply,**change))
                    else:
                        self.assertEqual(transport.capture(None,self.request,io.BytesIO()),reply['result'])
                        self.assertEqual(events,['closed','exited'])


if __name__ == '__main__': unittest.main()
