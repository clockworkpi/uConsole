from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from capture_pattern import verify_capture


class CapturePatternTests(unittest.TestCase):
    def encode(self, values):
        return b''.join(struct.pack('<I', x & 0xffffffff) for x in values)

    def test_complete_capture_and_counter_wrap(self):
        for start in (0, 100, 0xfffffff0):
            result = verify_capture(self.encode(range(start, start + 48000)))
            self.assertEqual(result['initial_sequence'], start)
            self.assertEqual(result['frames'], 48000)

    def test_whole_old_pattern_loss_and_duplicates_are_detected(self):
        for offset in (-256, -1, 1, 256):
            values = list(range(48000))
            values[20000:] = [x + offset for x in values[20000:]]
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, 'frame 20000'):
                verify_capture(self.encode(values))

    def test_channel_swap_and_silence_are_rejected(self):
        for payload in (b'\0' * 192000, b''.join(struct.pack('<HH', 0, x) for x in range(48000))):
            with self.assertRaisesRegex(ValueError, 'discontinuity'):
                verify_capture(payload)

    def test_length_and_frame_bounds(self):
        for payload in (b'', b'\0' * 191996, b'\0' * 192004):
            with self.assertRaisesRegex(ValueError, 'length'):
                verify_capture(payload)
        for frames in (0, True, 1.5, 48000 * 121):
            with self.assertRaisesRegex(ValueError, 'bounds'):
                verify_capture(b'', frames)
