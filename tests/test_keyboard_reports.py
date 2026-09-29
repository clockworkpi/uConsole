"""Pinned USBComposite encoding, firmware trace handling and refusal semantics."""
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from keyboard_reports import ASCII_TO_HID, Reports, encode_trace


class KeyboardReportTests(unittest.TestCase):
    def test_compiled_ascii_table_identity(self):
        self.assertEqual(len(ASCII_TO_HID), 128)
        self.assertEqual(hashlib.sha256(ASCII_TO_HID).hexdigest(),
                         '52369ccefa4602cc7e0143b423762911c89c830c9ca9ef6e099178b470b0c3d5')

    def test_keyboard_ascii_modifiers_and_special_keys(self):
        encoder = Reports()
        self.assertEqual(encoder.feed('keyboard_press', [ord('A'), 0, 0]),
                         [bytes.fromhex('020200040000000000')])
        self.assertEqual(encoder.feed('keyboard_press', [0x80, 0, 0]),
                         [bytes.fromhex('020300040000000000')])
        self.assertEqual(encoder.feed('keyboard_release', [ord('A'), 0, 0]),
                         [bytes.fromhex('020100000000000000')])
        self.assertEqual(encoder.feed('keyboard_press', [0xc2, 0, 0]),
                         [bytes.fromhex('0201003a0000000000')])
        self.assertEqual(encoder.feed('keyboard_release', [0x80, 0, 0])[0][1], 0)

    def test_rollover_preserves_slots_and_does_not_send_error_report(self):
        encoder = Reports()
        for key in 'abcdef':
            self.assertEqual(len(encoder.keyboard(ord(key), True)), 1)
        self.assertEqual(encoder.keyboard(ord('g'), True), [])
        self.assertEqual(encoder.keys, [4, 5, 6, 7, 8, 9])
        self.assertEqual(len(encoder.keyboard(ord('a'), True)), 1)  # duplicate sends
        encoder.keyboard(ord('c'), False)
        self.assertEqual(encoder.keyboard(ord('g'), True)[0], bytes.fromhex('02000004050a070809'))
        self.assertEqual(len(encoder.keyboard(ord('z'), False)), 1)  # absent release sends
        self.assertEqual(encoder.keyboard(0, True), [])
        self.assertEqual(encoder.keyboard(0, False), [])

    def test_modifier_release_is_not_reference_counted(self):
        encoder = Reports()
        encoder.keyboard(0x81, True)
        encoder.keyboard(ord('A'), True)
        encoder.keyboard(ord('A'), False)
        self.assertEqual(encoder.modifiers, 0)

    def test_caps_feedback_only_applies_when_enabled(self):
        encoder = Reports(leds=2)
        self.assertEqual(encoder.keycode(ord('a')), (4, 2))
        self.assertEqual(encoder.keycode(ord('A')), (4, 0))
        encoder.feed('caps_adjust', [0, 0, 0])
        self.assertEqual(encoder.keycode(ord('a')), (4, 0))
        encoder.feed('caps_adjust', [1, 0, 0])
        encoder.set_leds(0)
        self.assertEqual(encoder.keycode(ord('a')), (4, 0))

    def test_mouse_signed_bytes_duplicates_and_click_overwrite(self):
        encoder = Reports()
        self.assertEqual(encoder.feed('mouse_press', [1, 0, 0]), [bytes.fromhex('0101000000')])
        self.assertEqual(encoder.feed('mouse_press', [1, 0, 0]), [])
        self.assertEqual(encoder.feed('mouse_move', [-3, 5, -1]), [bytes.fromhex('0101fd05ff')])
        self.assertEqual(encoder.feed('mouse_click', [4, 0, 0]),
                         [bytes.fromhex('0104000000'), bytes.fromhex('0100000000')])
        self.assertEqual(encoder.buttons, 0)

    def test_consumer_encoding_and_release(self):
        encoder = Reports()
        self.assertEqual(encoder.feed('consumer_press', [0xe9, 0, 0]), [b'\x03\xe9\0'])
        self.assertEqual(encoder.feed('consumer_release', [0, 0, 0]), [b'\x03\0\0'])

    def test_joystick_packed_axes_defaults_clamping_buttons(self):
        encoder = Reports()
        encoder.feed('joystick_x', [511, 0, 0])
        encoder.feed('joystick_y', [2000, 0, 0])
        data = encoder.feed('joystick_button', [32, 1, 0])[0]
        self.assertEqual(len(data), 13)
        self.assertEqual(data[0], 20)
        bits = int.from_bytes(data[1:], 'little')
        self.assertEqual(bits & 0xffffffff, 0x80000000)
        self.assertEqual((bits >> 32) & 15, 15)
        self.assertEqual([(bits >> (36 + i * 10)) & 1023 for i in range(6)],
                         [511, 1023, 512, 512, 0, 0])
        encoder.feed('joystick_button', [32, 0, 0])
        self.assertEqual(encoder.joy_buttons, 0)

    def test_invalid_event_never_mutates_state(self):
        for name, values in [('joystick_button', [0, 1, 0]), ('joystick_button', [1, 2, 0]),
                             ('mouse_move', [0, 128, 0]), ('keyboard_press', [True, 0, 0]),
                             ('consumer_release', [1, 0, 0]), ('unknown', [0, 0, 0]),
                             ('keyboard_press', [65, 1, 0])]:
            encoder = Reports()
            before = repr(encoder.__dict__)
            with self.subTest(name=name, values=values), self.assertRaises(ValueError):
                encoder.feed(name, values)
            self.assertEqual(repr(encoder.__dict__), before)

    def test_trace_header_timestamps_and_non_report_events(self):
        records = [{'evidence': 'host-firmware-semantic-oracle', 'schema': 1},
                   {'us': 0, 'event': 'caps_adjust', 'args': [0, 0, 0]},
                   {'us': 10, 'event': 'pwm', 'args': [8, 500, 0]},
                   {'us': 10, 'event': 'keyboard_press', 'args': [ord('a'), 0, 0]}]
        self.assertEqual(encode_trace(records), [{'us': 10, 'report_hex': '020000040000000000'}])
        with self.assertRaisesRegex(ValueError, 'header'):
            encode_trace(records[1:])
        with self.assertRaisesRegex(ValueError, 'header'):
            encode_trace([{'evidence': 'host-firmware-semantic-oracle', 'schema': True}])
        records.append({'us': 9, 'event': 'state', 'args': [0, 0, 0]})
        with self.assertRaises(ValueError):
            encode_trace(records)
