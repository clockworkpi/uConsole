import unittest

from forge_watchdog_handoff import verify


class WatchdogHandoffTests(unittest.TestCase):
    def setUp(self):
        self.machine = 'a' * 32
        self.nonce = 'b' * 32
        self.before = dict(machine_id=self.machine, boot_id='11111111-1111-1111-1111-111111111111',
                           partition=1, tryboot=0, cmdline='root=PARTUUID=x')
        self.after = dict(self.before, boot_id='22222222-2222-2222-2222-222222222222', tryboot=1,
                          cmdline='root=PARTUUID=x uconsole.forge_trial=' + self.nonce + ' watchdog.open_timeout=120')
        self.watchdog = dict(open_timeout='120', state='active', identity='Broadcom BCM2835 Watchdog timer', owner_pids=[1], early_watchdog=True)

    def check(self):
        return verify(self.after, self.before, self.machine, self.nonce, 120, self.watchdog)

    def test_healthy_handoff_is_not_failure_recovery(self):
        self.assertFalse(self.check()['failed_boot_fallback_qualified'])

    def test_runtime_watchdog_alone_is_not_boot_handoff(self):
        self.after['cmdline'] = self.after['cmdline'].replace(' watchdog.open_timeout=120', '')
        with self.assertRaises(ValueError):
            self.check()

    def test_wrong_owner_or_timeout_is_not_handoff(self):
        for change in (dict(owner_pids=[]), dict(owner_pids=[2]), dict(state='inactive'), dict(open_timeout='0'), dict(early_watchdog=False)):
            original = self.watchdog
            self.watchdog = dict(original, **change)
            with self.assertRaises(ValueError):
                self.check()
            self.watchdog = original

    def test_duplicate_timeout_rejected(self):
        self.after['cmdline'] += ' watchdog.open_timeout=0'
        with self.assertRaises(ValueError):
            self.check()
