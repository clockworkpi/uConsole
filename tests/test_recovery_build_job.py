import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
import forge_recovery_build as host
import forge_recovery_build_worker as worker


class RecoveryBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.credentials = self.root/'credentials'
        self.credentials.mkdir(mode=0o700)
        for name in worker.CREDENTIALS:
            (self.credentials/name).write_bytes(b'private-build-canary')
            (self.credentials/name).chmod(0o600)
        self.boot = dict(machine_id='a'*32, boot_id='11111111-2222-3333-4444-555555555555',
                         cmdline='root=PARTUUID=21965b0c-02 rw', tryboot=0, partition=1)
        self.discovered = dict(host='fixture', boot=self.boot, kernel='6.12.62-v8+')
        self.output = self.root/'output'
        self.image = b'private-image-fixture'
        self.request = dict(schema=1, token='b'*32, boot=self.boot, kernel=self.discovered['kernel'],
            modules={name: '# fixture\n' for name in worker.MODULES},
            credentials={name: base64.b64encode(b'private-build-canary').decode() for name in worker.CREDENTIALS})

    def reply(self, command, **kwargs):
        request = json.loads(kwargs['input'])
        self.assertIn('StrictHostKeyChecking=yes', command)
        self.assertIn('sudo -n /usr/bin/python3 -I -S', command[-1])
        result = dict(status='built-not-published', token=request['token'], boot=request['boot'],
            kernel=request['kernel'], sha256=hashlib.sha256(self.image).hexdigest(), size=len(self.image),
            image='/var/tmp/uconsole-forge-build-'+request['token']+'/build/recovery.img',
            module_sha256={name: hashlib.sha256(source.encode()).hexdigest() for name, source in request['modules'].items()},
            contains_private_credentials=True, target_scratch_created=True, boot_files_written=False,
            publication_performed=False, reboot_performed=False)
        if hasattr(self, 'modify'): self.modify(result)
        kwargs['stdout'].write(json.dumps(result).encode()+b'\n'+getattr(self, 'transferred', self.image))
        return SimpleNamespace(returncode=0)

    def build(self):
        with patch.object(host.subprocess, 'run', side_effect=self.reply):
            return host.build(self.output, self.discovered, self.credentials)

    def test_private_output_is_verified_without_publication_or_reboot(self):
        result = self.build()
        self.assertEqual(result['status'], 'built-not-published')
        self.assertEqual((self.output/'recovery.img').read_bytes(), self.image)
        self.assertNotIn('private-build-canary', json.dumps(result))
        self.assertNotIn('image', result)
        for field in ('boot_files_written', 'publication_performed', 'reboot_performed', 'boot_qualified', 'root_write_authorized'):
            self.assertIs(result[field], False)
        for path in self.output.iterdir(): self.assertEqual(path.stat().st_mode & 0o077, 0)

    def test_changed_remote_identity_rejects_image(self):
        self.modify = lambda result: result.update(boot=dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555'))
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_corrupt_or_truncated_transfer_is_not_accepted(self):
        self.transferred = b'wrong'
        with self.assertRaises(ValueError): self.build()
        self.assertTrue((self.output/'failure.json').exists())
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_successful_transfer_with_wrong_hash_is_rejected(self):
        self.modify = lambda result: result.update(sha256='0'*64)
        with self.assertRaises(ValueError): self.build()
        self.assertFalse((self.output/'acceptance.json').exists())

    def test_existing_journal_never_replays_remote_build(self):
        self.output.mkdir()
        with patch.object(host.subprocess, 'run') as remote:
            with self.assertRaises(FileExistsError): host.build(self.output, self.discovered, self.credentials)
            remote.assert_not_called()

    def test_timeout_retains_uncertainty_and_does_not_retry(self):
        with patch.object(host.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 900)) as remote:
            with self.assertRaises(subprocess.TimeoutExpired):
                host.build(self.output, self.discovered, self.credentials)
            self.assertEqual(remote.call_count, 1)
        failed = json.loads((self.output/'failure.json').read_text())
        self.assertTrue(failed['target_scratch_may_exist'])
        self.assertFalse(failed['automatic_retry_performed'])

    def test_discovery_refuses_reboot_and_invalid_host_without_build(self):
        with patch.object(host, 'capture', side_effect=[self.boot, dict(self.boot, cmdline='root=changed')]), \
                patch.object(host.subprocess, 'run', return_value=SimpleNamespace(stdout='6.12.62-v8+\n')):
            with self.assertRaises(ValueError): host.discover('fixture')
        with patch.object(host.subprocess, 'run') as remote:
            with self.assertRaises(ValueError): host.discover('-oProxyCommand=bad')
            remote.assert_not_called()

    def native(self, observations=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(worker, 'SCRATCH_PARENT', self.root))
        stack.enter_context(patch.object(worker, 'prerequisites'))
        stack.enter_context(patch.object(worker, 'read_boot', side_effect=observations or [self.boot, self.boot]))
        stack.enter_context(patch.object(worker.os, 'uname', return_value=SimpleNamespace(release=self.discovered['kernel'])))
        def compile_image(command, **kwargs):
            self.assertEqual(command[:4], ['/usr/bin/python3', '-I', '-S', '-c'])
            directory = self.root/('uconsole-forge-build-'+self.request['token'])/'build'
            directory.mkdir(mode=0o700)
            image = directory/'recovery.img'
            image.write_bytes(self.image)
            image.chmod(0o600)
            worker.record(directory/'manifest.json', dict(kernel=self.request['kernel'], image=str(image),
                size=len(self.image), sha256=hashlib.sha256(self.image).hexdigest(),
                contains_private_credentials=True, boot_qualified=False, deployment_performed=False))
            return SimpleNamespace(returncode=0)
        stack.enter_context(patch.object(worker.subprocess, 'run', side_effect=compile_image))
        return stack

    def test_native_worker_builds_only_in_exclusive_private_scratch(self):
        with self.native(): result = worker.perform(self.request)
        directory = self.root/('uconsole-forge-build-'+self.request['token'])
        self.assertEqual(result['status'], 'built-not-published')
        self.assertTrue((directory/'acceptance.json').exists())
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in directory.rglob('*')))
        with self.native():
            with self.assertRaises(FileExistsError): worker.perform(self.request)

    def test_native_boot_change_precedes_any_staging(self):
        with self.native([dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')]):
            with self.assertRaises(ValueError): worker.perform(self.request)
        self.assertFalse((self.root/('uconsole-forge-build-'+self.request['token'])).exists())

    def test_native_boot_change_after_build_preserves_failed_scratch(self):
        with self.native([self.boot, dict(self.boot, boot_id='99999999-2222-3333-4444-555555555555')]):
            with self.assertRaises(ValueError): worker.perform(self.request)
        directory = self.root/('uconsole-forge-build-'+self.request['token'])
        self.assertTrue((directory/'failure.json').exists())
        self.assertFalse((directory/'acceptance.json').exists())

    def test_resource_allowlist_and_secret_bounds_reject_before_staging(self):
        for change in ('module', 'credential', 'token'):
            request = copy.deepcopy(self.request)
            if change == 'module': request['modules']['unexpected'] = 'bad'
            elif change == 'credential': request['credentials']['wpa.conf'] = 'x'*90001
            else: request['token'] = '../escape'
            with self.native(), self.assertRaises(ValueError): worker.perform(request)
        self.assertFalse((self.root/('uconsole-forge-build-'+self.request['token'])).exists())

    def test_wire_bootstrap_emits_header_then_exact_binary_without_native_build(self):
        image = self.root/'wire-fixture.img'
        image.write_bytes(b'\x00\xff\nprivate-fixture')
        result = dict(image=str(image), size=image.stat().st_size)
        packet = dict(modules={'forge_boot_observation': '# fixture only\n',
            'forge_recovery_build_worker': 'def perform(request):\n    return '+repr(result)+'\n'})
        child = subprocess.run([sys.executable, '-I', '-S', '-c', host.BOOTSTRAP],
            input=json.dumps(packet).encode(), capture_output=True, timeout=10, check=True)
        header, binary = child.stdout.split(b'\n', 1)
        self.assertEqual(json.loads(header), result)
        self.assertEqual(binary, image.read_bytes())

    def test_nonroot_prerequisite_fails_without_child_or_scratch(self):
        with patch.object(worker.os, 'geteuid', return_value=999), patch.object(worker.subprocess, 'run') as child:
            with self.assertRaises(ValueError): worker.prerequisites(self.request['kernel'])
            child.assert_not_called()
