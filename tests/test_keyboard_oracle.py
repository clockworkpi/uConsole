"""Actual firmware scan/layer/trackball code with logical GPIO and API recording.

These tests deliberately do not claim USB packet, rollover or electrical fidelity.
"""
import json
from pathlib import Path
import shlex
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class KeyboardOracleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / 'oracle'
        compiled = subprocess.run([*shlex.split(os.environ.get('CXX', 'c++')), '-std=c++17',
                                   '-I', str(ROOT / 'tools/keyboard-oracle'),
                                   str(ROOT / 'tools/keyboard-oracle/main.cpp'), '-o', str(cls.binary)],
                                  capture_output=True, text=True, timeout=60)
        if compiled.returncode:
            raise RuntimeError('Keyboard oracle compilation failed:\n' + compiled.stderr)

    def trace(self, commands):
        result = subprocess.run([str(self.binary)], input=commands, text=True,
                                capture_output=True, check=True, timeout=10)
        records = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(records[0]['evidence'], 'host-firmware-semantic-oracle')
        # setup() centers joystick axes, configures caps handling and extinguishes
        # lighting. Drop those calls, but keep all calls resulting from the trace.
        return [item for item in records[1:] if item['us'] >= 1500000]

    @staticmethod
    def calls(records, event):
        return [record['args'] for record in records if record['event'] == event]

    def test_base_character_matrix_scans_press_hold_release(self):
        keys = {
            **{i + 3: ord(c) for i, c in enumerate('`[]-=')},
            **{i + 8: ord(c) for i, c in enumerate('1234567890')},
            18: 0xb1, 19: 0xb3,
            **{i + 24: ord(c) for i, c in enumerate("qwertyuiopasdfghjklzxcvbnm,./\\;'")},
            56: 0xb2, 57: 0xb0, 60: ord(' '),
        }
        for address, key in keys.items():
            row, col = divmod(address, 8)
            with self.subTest(row=row, col=col):
                records = self.trace(f'matrix {row} {col} 1\nrun 15\n'
                                     f'matrix {row} {col} 0\nrun 15\n')
                self.assertEqual(self.calls(records, 'keyboard_press'), [[key, 0, 0]])
                self.assertEqual(self.calls(records, 'keyboard_release'), [[key, 0, 0]])

    def test_unpopulated_contacts_emit_nothing(self):
        for address in (20, 21, 22, 23, 61, 62, 63):
            row, col = divmod(address, 8)
            self.assertEqual(self.trace(f'matrix {row} {col} 1\nrun 10\n'
                                        f'matrix {row} {col} 0\nrun 10\n'), [])

    def test_matrix_bounce_is_filtered(self):
        records = self.trace('matrix 3 0 1\nrun 1\nmatrix 3 0 0\nrun 10\n')
        self.assertEqual(records, [])

    def test_direct_key_bounce_is_filtered(self):
        self.assertEqual(self.trace('key 0 1\nrun 1\nkey 0 0\nrun 10\n'), [])

    def test_keyboard_lock_suppresses_keys_until_fn_escape_unlocks(self):
        records = self.trace('matrix 7 2 1\nrun 10\nmatrix 2 2 1\nrun 10\n'
                             'matrix 2 2 0\nrun 10\nstate\n'
                             'matrix 3 0 1\nkey 0 1\nrun 10\n'
                             'matrix 3 0 0\nkey 0 0\nrun 10\n'
                             'matrix 2 2 1\nrun 10\nmatrix 2 2 0\nrun 10\n'
                             'matrix 7 2 0\nrun 10\nstate\n'
                             'matrix 3 0 1\nrun 10\nmatrix 3 0 0\nrun 10\n')
        self.assertEqual(self.calls(records, 'state'), [[1, 1, 0], [0, 0, 0]])
        self.assertEqual(self.calls(records, 'keyboard_press'), [[ord('q'), 0, 0]])
        self.assertEqual(self.calls(records, 'keyboard_release'), [[ord('q'), 0, 0]])

    def test_caps_lock_host_adjustment_calls(self):
        records = self.trace('matrix 7 2 1\nrun 10\nmatrix 2 3 1\nrun 10\n'
                             'matrix 2 3 0\nrun 10\n')
        self.assertEqual(self.calls(records, 'keyboard_press'), [[0xc1, 0, 0]])
        self.assertEqual(self.calls(records, 'keyboard_release'), [[0xc1, 0, 0]])
        self.assertEqual(self.calls(records, 'caps_adjust'), [[1, 0, 0], [0, 0, 0]])

    def test_fn_release_keeps_original_key_identity(self):
        records = self.trace('matrix 7 2 1\nrun 10\nmatrix 1 0 1\nrun 10\n'
                             'matrix 7 2 0\nrun 10\nmatrix 1 0 0\nrun 10\nstate\n')
        self.assertEqual(self.calls(records, 'keyboard_press'), [[0xc2, 0, 0]])
        self.assertEqual(self.calls(records, 'keyboard_release'), [[0xc2, 0, 0]])
        self.assertEqual(self.calls(records, 'state'), [[0, 0, 0]])

    def test_lighting_cycles_actual_firmware_levels(self):
        commands = 'matrix 7 2 1\nrun 10\n'
        commands += ('matrix 7 4 1\nrun 10\nmatrix 7 4 0\nrun 10\n') * 3
        records = self.trace(commands + 'state\n')
        self.assertEqual([call[1] for call in self.calls(records, 'pwm')], [500, 2000, 0])
        self.assertEqual(self.calls(records, 'state'), [[1, 0, 0]])

    def test_consumer_volume_shift_mute_and_brightness(self):
        records = self.trace('matrix 0 2 1\nrun 10\nmatrix 0 2 0\nrun 10\n'
                             'key 8 1\nrun 10\nmatrix 0 2 1\nrun 10\n'
                             'matrix 0 2 0\nkey 8 0\nrun 10\n'
                             'matrix 7 2 1\nrun 10\nmatrix 0 2 1\nrun 10\n'
                             'matrix 0 2 0\nrun 10\nmatrix 6 2 1\nrun 10\n'
                             'matrix 6 2 0\nrun 10\nmatrix 6 3 1\nrun 10\n'
                             'matrix 6 3 0\nrun 10\n')
        self.assertEqual(self.calls(records, 'consumer_press'),
                         [[v, 0, 0] for v in (0xea, 0xe9, 0xe2, 0x70, 0x6f)])
        self.assertEqual(len(self.calls(records, 'consumer_release')), 5)

    def test_all_direct_keys_in_keyboard_mode(self):
        keycodes = [0xda, 0xd9, 0xd8, 0xd7, ord('j'), ord('k'), ord('u'), ord('i'),
                    0x81, 0x85, 0x80, 0x84, 0x82, None, 0x86, None, None]
        for index, key in enumerate(keycodes):
            with self.subTest(index=index):
                records = self.trace(f'key {index} 1\nrun 10\nkey {index} 0\nrun 10\n')
                if key is None:
                    button = {13: 1, 15: 2, 16: 4}[index]
                    self.assertEqual(self.calls(records, 'mouse_press'), [[button, 0, 0]])
                    self.assertEqual(self.calls(records, 'mouse_release'), [[button, 0, 0]])
                else:
                    self.assertEqual(self.calls(records, 'keyboard_press'), [[key, 0, 0]])
                    self.assertEqual(self.calls(records, 'keyboard_release'), [[key, 0, 0]])

    def test_game_buttons_switch_but_dpad_remains_arrows(self):
        commands = 'switch 0\n'
        for index in range(8):
            commands += f'key {index} 1\nrun 10\nkey {index} 0\nrun 10\n'
        records = self.trace(commands)
        self.assertEqual(self.calls(records, 'keyboard_press'),
                         [[k, 0, 0] for k in (0xda, 0xd9, 0xd8, 0xd7)])
        self.assertEqual(self.calls(records, 'joystick_button'),
                         [[b, state, 0] for b in (2, 3, 1, 4) for state in (1, 0)])

    def test_select_trackball_wheel_uses_two_edges(self):
        records = self.trace('matrix 0 0 1\nrun 10\nedge 3\nrun 1\n'
                             'edge 3\nrun 1\nmatrix 0 0 0\nrun 10\n')
        self.assertEqual(self.calls(records, 'mouse_move'), [[0, 0, -1]])

    def test_trackball_direction_and_glide_are_deterministic(self):
        for edge, axis, sign in ((0, 0, -1), (1, 0, 1), (2, 1, -1), (3, 1, 1)):
            commands = f'run 1\nedge {edge}\nrun 40\n'
            records = self.trace(commands)
            self.assertEqual(records, self.trace(commands))
            movement = self.calls(records, 'mouse_move')
            self.assertTrue(movement)
            self.assertTrue(all(call[axis] * sign > 0 for call in movement))
            self.assertTrue(all(call[1 - axis] == 0 and call[2] == 0 for call in movement))

    def test_bad_trace_rejected(self):
        for command in ('matrix 8 0 1', 'key 17 1', 'edge 4', 'run -1',
                        'run 10001', 'advance 1 trailing', 'switch 2', 'unknown'):
            result = subprocess.run([str(self.binary)], input=command, text=True,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 2, command)
            self.assertIn('keyboard oracle:', result.stderr)
