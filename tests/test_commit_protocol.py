import copy
import unittest

from forge_recovery_commit_protocol import Protocol, decode


def fixture():
    plan=dict(binding=dict(boot_id='boot'),operation='install-hold',
              root_guard=dict(bytes=1024),prefix_guard=dict(bytes=512),suffix_guard=dict(bytes=0))
    return plan,Protocol(plan,'pin','attempt')


def progress(protocol, names):
    for name in names:
        protocol.consume(dict(type='progress',attempt='attempt',range=name,
                              bytes=protocol.plan[name+'_guard']['bytes'],total=protocol.plan[name+'_guard']['bytes']))


def prepared(protocol):
    return dict(type='prepared',attempt='attempt',pin='pin',binding=protocol.plan['binding'])


def unmounted():
    return dict(type='unmounted',attempt='attempt',pin='pin',boot_id='boot')


def complete():
    return dict(type='complete',attempt='attempt',result=dict(status='boot-file-commit-verified',
        plan_sha256='pin',boot_id='boot',operation='install-hold',file=dict(status='applied',path='/boot/firmware/config.txt'),
        root_written=False,boot_unmounted=True,reboot_performed=False,physical_boot_qualified=False))


class CommitProtocolTests(unittest.TestCase):
    def test_complete_order_and_renewal_boundary(self):
        _,protocol=fixture()
        self.assertTrue(protocol.may_renew)
        progress(protocol,('root','prefix'))
        protocol.consume(prepared(protocol))
        self.assertFalse(protocol.may_renew)
        protocol.committed()
        self.assertFalse(protocol.may_renew)
        protocol.consume(unmounted())
        self.assertTrue(protocol.may_renew)
        progress(protocol,('root',))
        protocol.consume(complete())
        self.assertFalse(protocol.may_renew)
        self.assertEqual(protocol.phase,'complete')

    def test_missing_hash_or_wrong_identity_prevents_prepared(self):
        for mode in ('missing','attempt','pin'):
            _,protocol=fixture()
            message=prepared(protocol)
            if mode!='missing':
                progress(protocol,('root','prefix'))
                message[mode]='wrong'
            with self.assertRaises(ValueError): protocol.consume(message)

    def test_progress_cannot_renew_during_commit(self):
        _,protocol=fixture()
        progress(protocol,('root','prefix'))
        protocol.consume(prepared(protocol))
        protocol.committed()
        with self.assertRaises(ValueError): progress(protocol,('root',))

    def test_completion_needs_unmount_and_posthash_and_exact_boolean_types(self):
        for failure in ('unmount','hash','boolean'):
            _,protocol=fixture()
            progress(protocol,('root','prefix'))
            protocol.consume(prepared(protocol))
            protocol.committed()
            if failure!='unmount': protocol.consume(unmounted())
            if failure=='boolean': progress(protocol,('root',))
            message=complete()
            if failure=='boolean': message['result']['root_written']=0
            with self.assertRaises(ValueError): protocol.consume(message)

    def test_duplicate_nonfinite_or_regressing_progress_rejected(self):
        for data in ('{"type":1,"type":2}', '{"type":NaN}'):
            with self.assertRaises(ValueError): decode(data)
        _,protocol=fixture()
        progress(protocol,('root',))
        with self.assertRaises(ValueError): progress(protocol,('root',))
        for done in (True,0,513):
            with self.assertRaises(ValueError):
                protocol.consume(dict(type='progress',attempt='attempt',range='prefix',bytes=done,total=512))


if __name__=='__main__': unittest.main()
