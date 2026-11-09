import unittest

from validate_recovery_watchdog import run, verify_lease_expiry


class LeaseValidationTests(unittest.TestCase):
    def test_soak_requires_explicit_mode_and_bounded_duration(self):
        for duration in (True, -1, 1, 329, 901, float('nan'), '360'):
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                run(None, None, None, None, None, None, None, require_lease=True,
                    require_python=True, lease_soak_seconds=duration)
        with self.assertRaises(ValueError):
            run(None, None, None, None, None, None, None, lease_soak_seconds=360)
    def test_requires_python_before_reading_inputs(self):
        with self.assertRaises(ValueError):
            run(None, None, None, None, None, None, None, require_lease=True)
        with self.assertRaises(ValueError):
            run(None, None, None, None, None, None, None, lease_watchdog_loss=True)

    def test_fault_injection_source_is_compilable_and_emulator_guarded(self):
        from forge_lease_watchdog_fault import SOURCE
        compile(SOURCE, '<lease-fault>', 'exec')
        self.assertLess(SOURCE.index('uconsole.emulator=1'), SOURCE.index('os.kill('))
        self.assertIn('stop(parent)\nstop(software)\nos.kill(keeper,signal.SIGKILL)', SOURCE)

    def test_requires_bounded_software_reboot_not_watchdog_or_panic(self):
        normal = 'reboot: Restarting system'
        verify_lease_expiry(0, 300, 300, normal)
        for code, elapsed, remaining, log in (
                (1, 300, 300, normal), (0, 20, 300, normal), (0, 421, 300, normal),
                (0, 300, 301, normal), (0, 300, 0, normal), (0, 300, 300, ''),
                (0, float('nan'), 300, normal), (0, 300, float('nan'), normal),
                (0, 300, 300, normal+' Kernel panic'),
                (0, 300, 300, normal+' Forge recovery failed'),
                (0, 300, 300, normal+' terminating on signal')):
            with self.subTest(code=code, elapsed=elapsed, log=log), self.assertRaises(ValueError):
                verify_lease_expiry(code, elapsed, remaining, log)
