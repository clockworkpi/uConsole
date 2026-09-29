import hashlib
import io
import json
import struct
import subprocess
import sys
import unittest

from forge_ram_transport import RecoveryProbe
from forge_recovery_bootplan import digest
from forge_recovery_restore_bootstrap import BOOTSTRAP, MAX_PACKET, argv, framed, payload, sources, start
from forge_recovery_restore_plan import INPUT_NAMES
from forge_recovery_restore_process import run as run_process
import test_ram_transport
import test_recovery_restore_plan


class RestoreBootstrapTests(unittest.TestCase):
    def minimal(self):
        io_source = sources()['forge_recovery_restore_io']
        return dict(schema=1, request={}, modules={
            'forge_recovery_restore_io': io_source,
            'forge_recovery_restore_worker':
                "from forge_fixture_helper import marker\n"
                "def run(request,channel):\n"
                "    data=channel.read(4)\n"
                "    channel.write(marker+data)\n",
            'forge_fixture_helper': "marker=b'TAIL'\n"})

    def packet(self, value):
        data = json.dumps(value, sort_keys=True).encode()
        return data, hashlib.sha256(data).hexdigest()

    def child(self, data, pin, *, wire=None, tail=b''):
        return subprocess.run([sys.executable, '-I', '-S', '-c', BOOTSTRAP, pin],
            input=(framed(data,pin) if wire is None else wire)+tail,
            capture_output=True, timeout=10)

    def test_lazy_owner_loader_preserves_bytes_after_length_framed_packet(self):
        data, pin = self.packet(self.minimal())
        result = self.child(data,pin,tail=b'next')
        self.assertEqual(result.returncode,0,result.stderr.decode())
        ready, body = result.stdout.split(b'\n',1)
        self.assertEqual(json.loads(ready),dict(type='bootstrap-ready',packet_sha256=pin))
        self.assertEqual(body,b'TAILnext')

    def test_wrong_packet_pin_rejected_before_owner_code_load(self):
        data, pin = self.packet(self.minimal())
        result = self.child(data,'0'*64,wire=framed(data,pin))
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,b'')
        self.assertIn(b'Owner packet pin differs',result.stderr)

    def test_oversized_or_incomplete_frame_rejected(self):
        for wire in (struct.pack('!Q',MAX_PACKET+1),b'bad',struct.pack('!Q',10)+b'{}'):
            result = self.child(b'{}','0'*64,wire=wire)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual(result.stdout,b'')

    def test_duplicate_json_and_invalid_module_names_fail_before_loading(self):
        value = self.minimal()
        value['modules']['os'] = "raise RuntimeError('must never load')"
        data, pin = self.packet(value)
        result = self.child(data,pin)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(result.stdout,b'')
        duplicate = b'{"schema":1,"schema":1}'
        pin = hashlib.sha256(duplicate).hexdigest()
        result = self.child(duplicate,pin)
        self.assertIn(b'Duplicate owner packet field',result.stderr)

    def test_missing_owner_dependency_cannot_fall_back_to_installed_modules(self):
        value = self.minimal()
        del value['modules']['forge_fixture_helper']
        data,pin = self.packet(value)
        result = self.child(data,pin)
        self.assertNotEqual(result.returncode,0)
        self.assertIn(b'Missing pinned owner module',result.stderr)
        self.assertEqual(result.stdout,b'')

    def owner_payload(self):
        fixture = test_recovery_restore_plan.RestorePlanTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        plan = fixture.compile()
        return payload(plan,digest(plan),dict(zip(INPUT_NAMES,fixture.arguments())),'e'*32)

    def test_entire_installed_worker_closure_imports_without_workspace_on_sys_path(self):
        data,_ = self.owner_payload()
        value = json.loads(data)
        self.assertGreater(len(value['modules']),30)
        # Stop before hardware inspection. This tests real closure imports and
        # the worker request gate, not fake physical identity or root access.
        value['request']['protocol'] = 999
        data,pin = self.packet(value)
        result = self.child(data,pin)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),dict(type='bootstrap-ready',packet_sha256=pin))
        self.assertIn(b'Unexpected restore worker request',result.stderr)
        self.assertNotIn(b'control-required',result.stdout)

    def test_framing_rejects_modified_source_and_plan_evidence(self):
        data,pin = self.owner_payload()
        with self.assertRaises(ValueError): framed(data+b' ',pin)
        value = json.loads(data)['request']
        value['inputs']['ram_boot_observation']['bootloader']['tryboot'] = 1
        with self.assertRaises(ValueError):
            payload(value['plan'],value['pin'],value['inputs'],value['attempt'])

    def test_ssh_argv_preserves_credential_pins_and_is_physical_only(self):
        fixture = test_ram_transport.RamTransportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        probe = RecoveryProbe(*fixture.args)
        command = argv(probe,'a'*64)
        for option in ('StrictHostKeyChecking=yes','IdentityAgent=none','ControlMaster=no',
                       'ClearAllForwardings=yes','GlobalKnownHostsFile=/dev/null'):
            self.assertIn(option,command)
        self.assertTrue(command[-1].endswith(' '+'a'*64))
        self.assertIn('/usr/bin/python3 -I -S -c',command[-1])
        with self.assertRaises(ValueError):
            argv(RecoveryProbe(*fixture.args,mode='emulated'),'a'*64)
        fixture.key.write_text('changed')
        with self.assertRaises(ValueError): argv(probe,'a'*64)

    def test_bounded_process_wrapper_loads_packet_and_continues_same_channel(self):
        data,pin = self.packet(self.minimal())
        def operation(channel,half_close,wait_success):
            start(channel,data,pin)
            self.assertEqual(channel.write(b'next'),4)
            half_close()
            output = bytearray()
            while True:
                part = channel.read(65536)
                if not part: break
                output.extend(part)
            wait_success()
            return bytes(output)
        output = run_process([sys.executable,'-I','-S','-c',BOOTSTRAP,pin],operation,
                             io.BytesIO(),timeout=10,idle_timeout=3)
        self.assertEqual(output,b'TAILnext')

    def test_readiness_must_echo_exact_pinned_packet_before_exchange(self):
        data,pin = self.packet(self.minimal())
        command = [sys.executable,'-I','-c',
                   "import sys;print('{\"type\":\"bootstrap-ready\",\"packet_sha256\":\"wrong\"}',flush=True);sys.stdin.buffer.read()"]
        def operation(channel,*_): start(channel,data,pin)
        with self.assertRaisesRegex(ValueError,'bootstrap readiness differs'):
            run_process(command,operation,io.BytesIO(),timeout=10,idle_timeout=3)


if __name__=='__main__': unittest.main()
