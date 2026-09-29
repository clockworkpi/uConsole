"""Explicit deck actions never bind editor/global host key events."""
import os
import gc
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_keyboard_gui import KeyboardDeck


@unittest.skipUnless(os.environ.get('DISPLAY') or sys.platform == 'darwin', 'Tk display required')
class KeyboardDeckTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.addCleanup(self.cleanup_tk)
        self.controller = MagicMock()
        self.runtime = self.controller.runtime.return_value
        self.runtime.args.keyboard = 'composite'
        self.runtime.identity = 'owned'
        self.runtime.process.poll.return_value = None
        self.controller.runtimes = {'gui': self.runtime}
        self.controller.submit_keyboard.return_value = {'job_id': 'job'}
        self.controller.job.return_value = {'status': 'completed'}
        self.deck = KeyboardDeck(self.root, self.controller, 'gui')

    def cleanup_tk(self):
        self.root.destroy()
        self.deck = self.root = self.controller = self.runtime = None
        # Reclaim callback/widget cycles on Tk's creating thread, before a
        # later executor test can trigger cyclic GC on a worker thread.
        gc.collect()

    def complete(self, held):
        self.deck.poll(held)

    def test_contacts_update_only_after_success_and_release_is_explicit(self):
        contact = ('matrix', 4, 2)
        self.deck.toggle(contact)
        self.controller.submit_keyboard.assert_called_once_with(
            'gui', [('matrix', 4, 2, 1), ('run', 10)])
        self.assertEqual(self.deck.held, set())
        self.deck.toggle(('matrix', 7, 2))
        self.assertEqual(self.controller.submit_keyboard.call_count, 1)
        self.complete({contact})
        self.deck.close()
        self.assertTrue(self.deck.window.winfo_exists())
        self.deck.release_all()
        self.controller.submit_keyboard.assert_called_with(
            'gui', [('matrix', 4, 2, 0), ('run', 10)])
        self.complete(set())
        self.deck.close()
        self.assertFalse(self.deck.window.winfo_exists())

    def test_uncertain_input_prevents_further_actions_until_vm_stops(self):
        self.deck.toggle(('key', 8))
        self.controller.job.return_value = {'status': 'failed'}
        self.complete({('key', 8)})
        self.assertTrue(self.deck.failed)
        self.deck.toggle(('key', 8))
        self.assertEqual(self.controller.submit_keyboard.call_count, 1)
        self.deck.close()
        self.assertTrue(self.deck.window.winfo_exists())
        self.runtime.process.poll.return_value = 0
        self.deck.close()
        self.assertFalse(self.deck.window.winfo_exists())

    def test_replacement_vm_and_generic_input_are_rejected(self):
        self.runtime.identity = 'replacement'
        self.deck.toggle(('matrix', 4, 2))
        self.controller.submit_keyboard.assert_not_called()
        self.runtime.args.keyboard = 'generic'
        with self.assertRaisesRegex(ValueError, 'composite'):
            KeyboardDeck(self.root, self.controller, 'gui')

    def test_deck_does_not_install_global_keyboard_bindings(self):
        self.assertEqual(self.root.bind_all('<KeyPress>'), '')
        self.assertEqual(self.root.bind_all('<KeyRelease>'), '')
        self.assertEqual(self.deck.window.bind('<KeyPress>'), '')

    def test_pointer_motion_requires_pad_focus_and_queues_firmware_edges(self):
        with patch.object(self.deck.window, 'focus_get', return_value=self.deck.typing):
            self.deck.pointer_motion(SimpleNamespace(x=0, y=0))
            self.controller.submit_keyboard.assert_not_called()
        with patch.object(self.deck.window, 'focus_get', return_value=self.deck.pointer):
            self.deck.pointer_motion(SimpleNamespace(x=0, y=0))
            self.deck.pointer_motion(SimpleNamespace(x=8, y=0))
        self.controller.submit_keyboard.assert_called_once_with('gui',
            [('edge', 1), ('run', 1), ('edge', 1), ('run', 1), ('run', 10)])
        self.complete(set())

    def test_initial_pointer_click_only_focuses_pad(self):
        with patch.object(self.deck.window, 'focus_get', return_value=self.deck.typing), \
                patch.object(self.deck.pointer, 'focus_set') as focus:
            self.deck.pointer_button(SimpleNamespace(num=1))
            focus.assert_called_once_with()
        self.controller.submit_keyboard.assert_not_called()

    def test_wheel_requires_explicit_opt_in_and_unheld_contacts(self):
        with patch.object(self.deck.window, 'focus_get', return_value=self.deck.pointer):
            self.deck.pointer_wheel(1)
            self.controller.submit_keyboard.assert_not_called()
            self.deck.select_scroll.set(True)
            self.deck.host_keys.change(38, 'a')
            self.deck.pointer_wheel(1)
            self.controller.submit_keyboard.assert_not_called()
            self.deck.host_keys.change(clear=True)
            self.deck.pointer_wheel(1)
            self.assertEqual(self.controller.submit_keyboard.call_count, 1)
            self.complete(set())

    def test_unfocused_wheel_does_not_accumulate_partial_notches(self):
        self.deck.select_scroll.set(True)
        with patch.object(self.deck.window, 'focus_get', return_value=self.deck.typing):
            self.deck.wheel_event(SimpleNamespace(delta=60))
        self.assertEqual(self.deck.wheel_remainder, 0)
        self.controller.submit_keyboard.assert_not_called()

    def test_typing_queues_release_while_press_is_running(self):
        event = SimpleNamespace(keycode=38, keysym='a')
        self.deck.host_event(event)
        self.deck.host_event(event, release=True)
        self.assertEqual(self.controller.submit_keyboard.call_count, 1)
        self.assertEqual(len(self.deck.host_queue), 1)
        self.complete({('matrix', 4, 2)})
        self.assertEqual(self.controller.submit_keyboard.call_count, 2)
        self.controller.submit_keyboard.assert_called_with(
            'gui', [('matrix', 4, 2, 0), ('run', 10)])
        self.complete(set())
        self.assertFalse(self.deck.host_active)

    def test_focus_loss_releases_host_holds_without_touching_editor(self):
        self.deck.host_event(SimpleNamespace(keycode=38, keysym='a'))
        self.complete({('matrix', 4, 2)})
        self.deck.host_event(SimpleNamespace(), clear=True)
        self.controller.submit_keyboard.assert_called_with(
            'gui', [('matrix', 4, 2, 0), ('run', 10)])
        self.complete(set())

    def test_adjacent_repeat_release_press_does_not_toggle_contact(self):
        event = SimpleNamespace(keycode=38, keysym='a')
        self.deck.key_press(event)
        self.complete({('matrix', 4, 2)})
        self.deck.key_release(event)
        self.deck.key_press(event)
        self.assertEqual(self.deck.pending_releases, {})
        self.assertEqual(self.controller.submit_keyboard.call_count, 1)
        self.deck.focus_out(SimpleNamespace())
        self.complete(set())
