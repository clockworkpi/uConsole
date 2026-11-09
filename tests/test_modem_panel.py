import gc
import os
import sys
import unittest
from unittest.mock import Mock


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'needs Tk display')
class ModemPanelTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from forge_modem_gui import ModemPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.controller = Mock(grants={'device-control'})
        self.controller.submit_modem.return_value = {'job_id':'fixture'}
        self.panel = ModemPanel(self.root, self.controller, 'demo')

    def tearDown(self):
        self.panel.job = None
        self.panel.close()
        self.root.update_idletasks()
        self.root.destroy()
        gc.collect()

    def test_typed_request_and_pending_job_are_not_replayed(self):
        self.panel.value.set('3')
        self.panel.apply.invoke()
        self.controller.submit_modem.assert_called_once_with('demo', {'registration':3})
        self.assertTrue(self.panel.apply.instate(['disabled']))
        self.assertFalse(self.panel.close())
        self.panel.submit()
        self.controller.submit_modem.assert_called_once()
        self.controller.job.return_value = {'status':'completed', 'result':{'observed':{'registration':3}}}
        self.panel.poll()
        self.assertIsNone(self.panel.job)
        self.assertIn('observed', self.panel.status.get())

    def test_denied_permission_disables_apply(self):
        self.controller.grants = set()
        self.panel.enable()
        self.panel.apply.invoke()
        self.controller.submit_modem.assert_not_called()
        self.panel.query.invoke()
        self.controller.submit_modem.assert_called_once_with('demo')

    def test_status_transport_failure_keeps_job_for_read_only_recheck(self):
        self.panel.submit()
        self.controller.job.side_effect = ConnectionError('lost status')
        self.panel.poll()
        self.assertEqual(self.panel.job, 'fixture')
        self.assertIsNone(self.panel.timer)
        self.assertIn('Do not resubmit', self.panel.status.get())
        self.controller.job.side_effect = None
        self.controller.job.return_value = {'status':'failed', 'error':'uncertain effect'}
        self.panel.refresh.invoke()
        self.assertIsNone(self.panel.job)
        self.assertIn('no automatic retry', self.panel.status.get())
        self.controller.submit_modem.assert_called_once()

    def test_sim_selection_is_string_and_invalid_value_never_submitted(self):
        self.panel.field.set('sim')
        self.panel.select_field()
        self.panel.value.set('absent')
        self.panel.submit()
        self.controller.submit_modem.assert_called_once_with('demo', {'sim':'absent'})

    def test_invalid_selection_rejected(self):
        self.panel.value.set('6')
        self.panel.submit()
        self.controller.submit_modem.assert_not_called()
        self.assertIn('supported', self.panel.status.get())

    def test_cable_control_uses_same_pending_job_and_grants(self):
        self.controller.grants = set()
        self.panel.enable()
        self.panel.cable_buttons[1].invoke()
        self.controller.submit_modem.assert_not_called()
        self.controller.grants = {'device-control'}
        self.panel.enable()
        self.panel.cable_buttons[1].invoke()
        self.controller.submit_modem.assert_called_once_with('demo', connected=False)
        self.assertTrue(all(button.instate(['disabled']) for button in self.panel.cable_buttons))
