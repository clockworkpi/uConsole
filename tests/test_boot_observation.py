import unittest
from forge_boot_observation import verify


class BootObservationTests(unittest.TestCase):
    def setUp(self):
        self.before = dict(machine_id='a'*32, boot_id='12345678-1234-1234-1234-123456789abc',
                           cmdline='root=PARTUUID=1234-02 quiet', tryboot=0, partition=1)
        self.after = dict(self.before, boot_id='22345678-1234-1234-1234-123456789abc', tryboot=1,
                          cmdline=self.before['cmdline'] + ' uconsole.forge_trial=' + 'b'*32)

    def test_trial_and_normal_require_new_boots(self):
        self.assertEqual(verify(self.after,self.before,'a'*32,'b'*32)['status'],'verified-trial')
        normal = dict(self.before, boot_id='32345678-1234-1234-1234-123456789abc')
        self.assertEqual(verify(normal,self.after,'a'*32)['status'],'verified-normal')

    def test_wrong_identity_flag_nonce_root_or_old_boot_fails(self):
        for changes in (dict(machine_id='c'*32),dict(tryboot=0),dict(tryboot=True),
                        dict(boot_id=self.before['boot_id']),dict(partition=2),
                        dict(cmdline='root=wrong uconsole.forge_trial='+'b'*32),
                        dict(cmdline=self.after['cmdline']+' uconsole.forge_trial='+'b'*32),
                        dict(cmdline=self.after['cmdline']+' uconsole.emulator=1')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verify(dict(self.after,**changes),self.before,'a'*32,'b'*32)
