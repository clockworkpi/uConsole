"""Host-key identity, shared modifiers and ordered firmware scan transitions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_keyboard_host import HostKeys, HostPointer, FN, SHIFT, contacts, wheel_commands
from forge_keyboard import commands_snapshot


class HostKeysTests(unittest.TestCase):
    def test_wheel_is_bounded_and_contains_select_press_release(self):
        self.assertEqual(wheel_commands(0), [])
        for count in (-8, -1, 1, 8):
            commands = wheel_commands(count)
            commands_snapshot(commands)
            self.assertEqual(commands[0], ('matrix', 0, 0, 1))
            self.assertEqual(commands[-2], ('matrix', 0, 0, 0))
            self.assertEqual(sum(c[0] == 'edge' for c in commands), abs(count) * 2)
        for count in (True, 9, -9, 1.0):
            with self.assertRaises(ValueError):
                wheel_commands(count)
    def test_pointer_retains_subedge_motion_and_signs(self):
        pointer = HostPointer()
        self.assertEqual(pointer.move(0, 0), [])
        self.assertEqual(pointer.move(3, 0), [])
        self.assertEqual(pointer.move(4, 0), [[('edge', 1), ('run', 1), ('run', 10)]])
        self.assertEqual(pointer.move(0, -4), [[('edge', 0), ('run', 1),
                                               ('edge', 2), ('run', 1), ('run', 10)]])
        pointer.reset()
        self.assertEqual(pointer.move(500, 500), [])

    def test_pointer_batches_are_bounded_and_jump_rejection_preserves_anchor(self):
        pointer = HostPointer()
        pointer.move(0, 0)
        batches = pointer.move(256, 256)
        self.assertEqual(sum(command[0] == 'edge' for batch in batches for command in batch), 128)
        for batch in batches:
            commands_snapshot(batch)
        with self.assertRaisesRegex(ValueError, '128'):
            pointer.move(900, 900)
        self.assertEqual(pointer.anchor, (256, 256))

    def test_mouse_buttons_use_production_direct_contacts(self):
        self.assertEqual(contacts('Mouse1'), {('key', 13)})
        self.assertEqual(contacts('Mouse2'), {('key', 16)})
        self.assertEqual(contacts('Mouse3'), {('key', 15)})

    def test_repeated_press_and_changed_release_keysym(self):
        state = HostKeys()
        pressed, _ = state.change(38, 'a')
        self.assertEqual(pressed, [('matrix', 4, 2, 1), ('run', 10)])
        self.assertEqual(state.change(38, 'A')[0], [])
        released, held = state.change(38, 'A', release=True)
        self.assertEqual(released, [('matrix', 4, 2, 0), ('run', 10)])
        self.assertEqual(held, set())

    def test_function_chords_settle_fn_first_and_share_it(self):
        state = HostKeys()
        commands, _ = state.change(67, 'F1')
        self.assertEqual(commands[:2], [(*FN, 1), ('run', 10)])
        state.change(68, 'F2')
        self.assertNotIn((*FN, 0), state.change(67, 'F1', release=True)[0])
        commands, held = state.change(68, 'F2', release=True)
        self.assertEqual(commands[-2:], [(*FN, 0), ('run', 10)])
        self.assertEqual(held, set())

    def test_shifted_symbols_and_focus_loss(self):
        state = HostKeys()
        self.assertEqual(contacts('exclam'), {SHIFT, ('matrix', 1, 0)})
        self.assertEqual(contacts('quotedbl'), {SHIFT, ('matrix', 6, 7)})
        self.assertEqual(contacts('unknown'), set())
        state.change(50, 'Shift_L')
        state.change(38, 'A')
        commands, held = state.change(clear=True)
        self.assertEqual(held, set())
        self.assertEqual(commands[-2:], [(*SHIFT, 0), ('run', 10)])
        self.assertEqual(state.change(clear=True)[0], [])
