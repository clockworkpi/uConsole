import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import forge_bootcommit_probe as probe
from forge_recovery_bootcommit import validate
from validate_recovery_watchdog import run
import test_ram_identity


class BootCommitProbeTests(unittest.TestCase):
    def setUp(self):
        self.review, self.image = probe.fixture('a'*32)
        self.binding = dict(nonce='a'*32,kernel='6.12.62-v8+',serial=None,mode='emulated',
            boot_id='12345678-1234-1234-1234-123456789abc',cid='a'*32,disk_id='f047e001',
            device='/dev/mmcblk1',extent=dict(offset_bytes=512,length_bytes=1024,disk_bytes=1536))
        self.hashes = dict(sha256='c'*64,root_sha256='d'*64,protected_prefix_sha256='e'*64)
        self.root = dict(offset=512,bytes=1024,sha256='d'*64)

    def test_fixture_builds_valid_inverse_plans_with_independent_root(self):
        self.assertEqual(self.review['image_dependency']['sha256'], hashlib.sha256(self.image).hexdigest())
        plans = []
        for operation in ('install-hold','release-hold'):
            plan, pin = probe.transition(self.review,self.binding,self.hashes,self.root,operation)
            validate(plan,pin)
            plans.append(plan)
        self.assertEqual(plans[0]['before'], plans[1]['after'])
        self.assertEqual(plans[0]['after'], plans[1]['before'])
        with self.assertRaisesRegex(ValueError, 'independently approved root'):
            probe.transition(self.review,self.binding,dict(self.hashes,root_sha256='f'*64),self.root,'release-hold')

    def test_cli_needs_disposable_boot_writes_and_lease(self):
        for options in ({}, {'boot_write_roundtrip':True}, {'require_lease':True}):
            with self.assertRaisesRegex(ValueError,'CONFIG commit validation'):
                run(None,None,None,None,None,None,None,boot_commit_roundtrip=True,**options)

    def test_physical_session_rejected_before_any_device_open(self):
        fixture = test_ram_identity.RamIdentityTests()
        fixture.setUp()
        namespace = {}
        source = probe.script(dict(phase='seed',binding=self.binding))
        # Read only the generated request assignment; replace the RAM reader
        # with a physical-session observation, retaining the real verifier.
        exec(source.split('\n',1)[0],namespace)
        request = namespace['request']
        for index,(name,module) in enumerate(request['modules']):
            if name == 'forge_ram_identity':
                request['modules'][index] = (name,module+'\nREADER='+repr('print('+repr(json.dumps(fixture.record))+')'))
        with patch.dict(sys.modules), patch.object(sys,'path',list(sys.path)), patch('os.open') as opened:
            with self.assertRaises(ValueError):
                exec(probe.SOURCE,dict(request=request))
            opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
