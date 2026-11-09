from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from forge_recovery_runtime import INIT, DHCP, SSHD_CONFIG, NETWORK_OBSERVER


class RecoveryRuntimeTests(unittest.TestCase):
    def test_network_observer_reports_flags_without_log_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'forge-interface').write_text('wlan0\n')
            (root/'wpa.log').write_text('CTRL-EVENT-CONNECTED private-ssid\n'
                                       'CTRL-EVENT-ASSOC-REJECT private-bssid\n'
                                       'CTRL-EVENT-SSID-TEMP-DISABLED secret-value\n')
            (root/'forge-dhcp.state').write_text('bound\n')
            script = NETWORK_OBSERVER.replace('/run/', directory+'/').replace('/dev/kmsg', directory+'/no-kmsg')
            # Stub commands inside the test shell; never inspect host links or
            # sleep for the production sampling interval.
            prefix = 'sleep() { :; }\nip() { echo "inet 192.0.2.1/24"; }\n'
            result = subprocess.run(['sh', '-c', prefix+script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = (root/'forge-network-status.log').read_text().splitlines()
            self.assertEqual(len(lines), 6)
            expected = ('Forge recovery network: present=1 ipv4=1 associated_seen=1 '
                        'assoc_reject_seen=1 auth_disabled_seen=1 lease_bound=1')
            self.assertEqual(lines, [expected]*6)
            self.assertFalse((root/'no-kmsg').exists())
            self.assertEqual(result.stdout, '')

    def test_network_observer_rejects_unexpected_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'forge-interface').write_text('eth99\n')
            script = NETWORK_OBSERVER.replace('/run/', directory+'/')
            result = subprocess.run(['sh', '-c', script], capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root/'forge-network-status.log').exists())

    def test_dhcp_records_bound_only_after_configuration_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'forge-interface').write_text('wlan0\n')
            script=DHCP.replace('/run/', directory+'/')
            env={'interface':'wlan0','ip':'192.0.2.2','subnet':'255.255.255.0','router':'192.0.2.1'}
            for route_result in (1, 0):
                prefix='ifconfig() { return 0; }\nip() { return '+str(route_result)+'; }\n'
                result=subprocess.run(['sh','-c',prefix+script,'dhcp','bound'],env=env,capture_output=True)
                self.assertEqual(result.returncode,route_result)
                state=root/'forge-dhcp.state'
                if route_result:
                    self.assertFalse(state.exists())
                else:
                    self.assertEqual(state.read_text(),'bound\n')

    def test_failure_logs_stage_before_reboot_even_if_kernel_log_unavailable(self):
        # Run the real failure function with redirected test sinks and a fake
        # reboot that exits the shell. No host devices are opened.
        function = INIT.split('fail() {', 1)[1].split('\n}\n', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            console = Path(directory) / 'console'
            kmsg = Path(directory) / 'kmsg'
            function = function.replace('/dev/console', str(console)).replace('/dev/kmsg', str(kmsg))
            script = 'stage=network-interface\nreboot() { exit 42; }\nfail() {' + function + '\n}\nfail\n'
            result = subprocess.run(['sh', '-c', script], capture_output=True)
            self.assertEqual(result.returncode, 42)
            self.assertIn('failed at network-interface', console.read_text())
            self.assertFalse(kmsg.exists())

    def test_failure_kernel_marker_is_bounded_and_precedes_reboot(self):
        function = INIT.split('fail() {', 1)[1].split('\n}\n', 1)[0]
        self.assertLess(function.index('>/dev/kmsg'), function.index('reboot -f'))
        self.assertIn('test -c /dev/kmsg', function)
        self.assertNotIn('cat ', function)
        self.assertNotIn('wpa.log', function)

    def run_interface_wait(self, ready_after):
        # Execute the actual init fragment with shell functions, never touching
        # the host network, sleeping, or invoking a real reboot.
        fragment = INIT.split('stage=network-interface\n', 1)[1].split(
            'if test "$network_interface" = wlan0;', 1)[0]
        script = 'ready_after=' + str(ready_after) + '\n' + '''network_interface=wlan0
checks=0
sleeps=0
ip() {
    test "$*" = "link show dev wlan0" || exit 98
    checks=$((checks + 1))
    test "$checks" -gt "$ready_after"
}
sleep() { sleeps=$((sleeps + 1)); }
fail() { echo "failed:$checks:$sleeps"; exit 42; }
'''
        return subprocess.run(['sh', '-c', script + fragment +
            '\necho "ready:$checks:$sleeps"'], capture_output=True, text=True)

    def test_interface_can_appear_after_driver_returns(self):
        result = self.run_interface_wait(3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ready:4:3')

    def test_missing_interface_fails_after_bounded_wait(self):
        result = self.run_interface_wait(99)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual(result.stdout.strip(), 'failed:21:20')

    def test_existing_interface_does_not_delay(self):
        result = self.run_interface_wait(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'ready:1:0')

    def test_keeper_is_opt_in_and_ready_before_networking(self):
        self.assertIn('uconsole.recovery_watchdog=1', INIT)
        self.assertIn('kill -0 "$keeper"', INIT)
        self.assertIn('test "$attempts" -le 5 || fail', INIT)
        self.assertLess(INIT.index('/usr/sbin/forge-watchdog'), INIT.index('wpa_supplicant -B'))
        self.assertLess(INIT.index('/run/forge-watchdog.ready'), INIT.index('sshd -D'))

    @unittest.skipUnless(shutil.which('shellcheck'), 'shellcheck required')
    def test_runtime_and_dhcp_shellcheck(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name, content in (('init', INIT), ('dhcp', DHCP), ('observer', NETWORK_OBSERVER)):
                path = Path(directory) / name
                path.write_text(content)
                paths.append(str(path))
            subprocess.run(['shellcheck', '-s', 'sh', *paths], check=True)

    def test_dhcp_rejects_other_interface_before_any_commands(self):
        result = subprocess.run(['sh', '-c', DHCP, 'dhcp', 'bound'],
                                env={'interface': 'eth99'}, capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_no_password_or_forwarding_in_recovery_sshd(self):
        fields = dict(line.split(None, 1) for line in SSHD_CONFIG.splitlines())
        self.assertEqual(fields['AuthenticationMethods'], 'publickey')
        self.assertEqual(fields['PasswordAuthentication'], 'no')
        self.assertEqual(fields['KbdInteractiveAuthentication'], 'no')
        self.assertEqual(fields['DisableForwarding'], 'yes')
        self.assertEqual(fields['UsePAM'], 'no')
