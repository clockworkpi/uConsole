from dataclasses import asdict
import copy
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forge_recovery_commit_liveness import verify_lease, budget, private_json
from forge_recovery_lease import Lease


class CommitLivenessTests(unittest.TestCase):
    def setUp(self):
        initial=Lease.start('a'*32,'12345678-1234-1234-1234-123456789abc','b'*64,100)
        self.lease=initial.renew(dict(schema=1,purpose='offline-backup',nonce=initial.nonce,
            boot_id=initial.boot_id,owner=initial.owner,sequence=1,seconds=300),150)
        self.binding=dict(nonce=initial.nonce,boot_id=initial.boot_id)

    def test_bound_current_host_renewal(self):
        self.assertEqual(verify_lease(asdict(self.lease),self.binding,self.lease.owner,self.lease.receipt(),151),self.lease)

    def test_changed_identity_sequence_expiry_and_nonfinite_state(self):
        for key,value in (('owner','c'*64),('nonce','c'*32),('sequence',True),('sequence',2),
                          ('started',float('nan')),('sampled',500),('last_seconds',0),('deadline',1000)):
            changed=dict(asdict(self.lease),**{key:value})
            with self.subTest(key=key),self.assertRaises(ValueError):
                verify_lease(changed,self.binding,self.lease.owner,self.lease.receipt(),151)
        with self.assertRaises(ValueError):
            verify_lease(asdict(self.lease),self.binding,self.lease.owner,self.lease.receipt(),450)

    def test_backup_receipt_does_not_grant_authority_or_accept_integer_booleans(self):
        for value in (True,0):
            receipt=dict(self.lease.receipt(),root_write_authorized=value)
            with self.assertRaises(ValueError):
                verify_lease(asdict(self.lease),self.binding,self.lease.owner,receipt,151)

    def test_watchdog_fd_ownership_activity_timeout_and_pid_are_checked(self):
        process_record='123 (python3) '+' '.join(['S']+['0']*18+['42'])
        values={'/proc/cmdline':'uconsole.recovery_lease=1 uconsole.recovery_owner='+self.lease.owner,
                '/proc/123/stat':process_record,'/sys/class/watchdog/watchdog0/dev':'10:130',
                '/sys/class/watchdog/watchdog0/state':'active','/sys/class/watchdog/watchdog0/timeout':'15'}
        def local(path):
            return asdict(self.lease) if 'deadline' in path else dict(pid=123,maximum_lifetime=86400,lease=True)
        for failure in (None,'state','timeout','owner','pid','fd'):
            current=dict(values)
            if failure=='state': current['/sys/class/watchdog/watchdog0/state']='inactive'
            if failure=='timeout': current['/sys/class/watchdog/watchdog0/timeout']='1'
            if failure=='owner': current['/proc/cmdline']='uconsole.recovery_lease=1'
            if failure=='pid': current['/proc/123/stat']=process_record.replace(') S ',') Z ')
            descriptor=SimpleNamespace(stat=lambda:SimpleNamespace(st_mode=stat.S_IFCHR,
                st_rdev=os.makedev(10,131 if failure=='fd' else 130)))
            with patch('forge_recovery_commit_liveness.private_json',side_effect=local), \
                    patch.object(Path,'read_text',lambda path:current[str(path)]), \
                    patch.object(Path,'iterdir',return_value=iter([descriptor])), patch.object(os,'kill'), \
                    patch('forge_recovery_commit_liveness.time.monotonic',side_effect=[151,152]):
                if failure is None:
                    self.assertEqual(budget(self.binding,self.lease.owner,self.lease.receipt()),298)
                else:
                    with self.subTest(failure=failure),self.assertRaises(ValueError):
                        budget(self.binding,self.lease.owner,self.lease.receipt())

    def test_private_liveness_record_requires_stable_owned_bounded_single_file(self):
        payload=b'{"status":"fixture"}'
        valid=dict(st_mode=stat.S_IFREG|0o600,st_uid=0,st_nlink=1,st_size=len(payload),st_mtime_ns=1,st_ctime_ns=1)
        for change in ({},{'st_uid':1000},{'st_nlink':2},{'st_size':4097},{'st_mode':stat.S_IFREG|0o644}):
            with patch.object(os,'open',return_value=123),patch.object(os,'close') as closed, \
                    patch.object(os,'fstat',return_value=SimpleNamespace(**dict(valid,**change))), \
                    patch.object(os,'read',return_value=payload):
                if change:
                    with self.assertRaises(ValueError): private_json('/fixture')
                else:
                    self.assertEqual(private_json('/fixture'),dict(status='fixture'))
                closed.assert_called_once_with(123)


if __name__=='__main__': unittest.main()
