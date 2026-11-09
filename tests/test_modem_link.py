import unittest
import socket
from unittest.mock import patch

from forge_modem_at import Modem, ModemState
from serve_modem_at import qmp_link, serve_channels, serve_cli


class ModemLinkTests(unittest.TestCase):
    def test_uncertain_initial_sync_is_not_retried_on_cleanup(self):
        a, b = socket.socketpair()
        updates = []
        def failure(up):
            updates.append(up)
            raise TimeoutError('No acknowledgement')
        try:
            with self.assertRaises(TimeoutError):
                serve_channels({'primary': a}, ModemState(sim='pin'), failure)
            self.assertEqual(updates, [False])
        finally:
            a.close()
            b.close()

    def test_cli_interrupt_waits_for_link_down(self):
        a, b = socket.socketpair()
        updates = []
        class InterruptedSelector:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def register(self, *args):
                pass
            def select(self):
                raise KeyboardInterrupt
        try:
            a.settimeout(3)
            with patch('serve_modem_at.selectors.DefaultSelector', InterruptedSelector):
                serve_cli({'primary': a}, ModemState(), updates.append)
            self.assertEqual(updates, [True, False])
            self.assertEqual(a.gettimeout(), 3)
        finally:
            a.close()
            b.close()

    def test_shutdown_link_failure_is_not_swallowed(self):
        def failure(*args, **kwargs):
            raise ConnectionError('QEMU stopped too early')
        with patch('serve_modem_at.serve_channels', failure):
            with self.assertRaises(ConnectionError):
                serve_cli({}, ModemState())

    def test_pin_unlock_radio_batch_and_roaming(self):
        links = []
        modem = Modem(ModemState(sim='pin'), link_changed=links.append)
        self.assertEqual(links, [False])
        modem.feed('primary', b'AT+CPIN="1234"\rAT+CFUN=4\rAT+CFUN=1\r')
        self.assertEqual(links, [False, True, False, True])
        modem.set_scenario(registration=5)
        self.assertEqual(links, [False, True, False, True])
        modem.set_scenario(registration=2)
        self.assertEqual(links[-1], False)
        modem.set_scenario(registration=1)
        self.assertEqual(links[-1], True)
        modem.set_scenario(sim='absent')
        self.assertEqual(links[-1], False)

    def test_uncertain_link_update_never_returns_success_or_retries(self):
        links = []
        def update(up):
            links.append(up)
            if not up:
                raise TimeoutError('lost acknowledgement')
        modem = Modem(link_changed=update)
        with self.assertRaises(TimeoutError):
            modem.feed('primary', b'AT+CFUN=4\r')
        with self.assertRaises(RuntimeError):
            modem.feed('secondary', b'AT\r')
        with self.assertRaises(RuntimeError):
            modem.set_scenario(radio=1)
        self.assertEqual(links, [True, False])

    def test_explicit_qmp_device_and_port(self):
        with patch('uconsole_emulator.qmp') as qmp:
            update = qmp_link(4444, 'modem-device')
            update(False)
            qmp.assert_called_once_with(4444, 'set_link', {'name':'modem-device', 'up':False})
        for port, name in ((0,'modem'), (True,'modem'), (4444,''), (4444,'../modem')):
            with self.assertRaises(ValueError):
                qmp_link(port, name)
