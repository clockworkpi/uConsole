import unittest

from forge_modem_at import Modem


class PacketDataTests(unittest.TestCase):
    def setUp(self):
        self.links = []
        self.modem = Modem(link_changed=self.links.append)
        for port in ('primary', 'secondary'):
            self.modem.feed(port, b'ATE0\r')

    def command(self, value, port='primary'):
        return self.modem.feed(port, value.encode()+b'\r')[port]

    def test_define_activate_query_and_deactivate_shared_context(self):
        self.assertEqual(self.command('AT+CGACT=0,1'), b'\r\nOK\r\n')
        self.assertEqual(self.links, [True, False])
        self.assertEqual(self.command('AT+CGDCONT=1,"IP","Test.APN"'), b'\r\nOK\r\n')
        self.assertIn(b'1,"IP","Test.APN","",0,0,0,0', self.command('AT+CGDCONT?', 'secondary'))
        self.assertEqual(self.command('AT+CGACT=1,1', 'secondary'), b'\r\nOK\r\n')
        self.assertEqual(self.links, [True, False, True])
        self.assertEqual(self.command('AT+CGACT?'), b'\r\n+CGACT: 1,1\r\nOK\r\n')

    def test_detach_clears_bearers_reattach_does_not_activate(self):
        self.command('AT+CGATT=0')
        self.assertFalse(self.modem.state.network_ready())
        self.assertEqual(self.command('AT+CGACT=1,1'), b'\r\nERROR\r\n')
        self.command('AT+CGATT=1')
        self.assertFalse(self.modem.state.network_ready())
        self.command('AT+CGACT=1')
        self.assertTrue(self.modem.state.network_ready())

    def test_activation_without_service_fails(self):
        self.command('AT+CGACT=0')
        self.modem.set_scenario(registration=3)
        self.assertEqual(self.command('AT+CGACT=1'), b'\r\nERROR\r\n')
        self.assertEqual(self.command('AT+CGATT=1'), b'\r\nERROR\r\n')
        self.assertEqual(self.command('AT+CGACT?'), b'\r\n+CGACT: 1,0\r\nOK\r\n')

    def test_unsupported_values_are_atomic_and_active_definition_protected(self):
        for command in ('AT+CGDCONT=1,"IP","changed"', 'AT+CGDCONT=25,"IP","test"',
                        'AT+CGDCONT=2,"IPV6","test"', 'AT+CGACT=1,2',
                        'AT+CGACT=2,1', 'AT+CGDCONT=2,"IP","x",0,1'):
            self.assertEqual(self.command(command), b'\r\nERROR\r\n')
        self.assertEqual(self.modem.state.packet.contexts, {1:''})
        self.assertEqual(self.modem.state.packet.active, {1})
        self.assertEqual(self.links, [True])

    def test_multiple_contexts_and_delete(self):
        self.command('AT+CGDCONT=100,"IP","second"')
        self.command('AT+CGACT=1,100')
        self.command('AT+CGACT=0,1')
        self.assertEqual(self.links, [True])
        self.command('AT+CGACT=0,100')
        self.assertEqual(self.links, [True, False])
        self.command('AT+CGDCONT=100')
        self.assertNotIn(100, self.modem.state.packet.contexts)
