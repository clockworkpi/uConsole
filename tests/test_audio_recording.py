import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from verify_audio_recording import verify


class AudioRecordingTests(unittest.TestCase):
    def test_repeated_streams_require_every_complete_tone(self):
        tone = b''.join(struct.pack('<hh', x, x) for x in
                       (int(4096 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'repeated.wav'
            for gap in (0, 960):
                for truncated in (False, True):
                    with self.subTest(gap=gap, truncated=truncated):
                        with wave.open(str(path), 'wb') as stream:
                            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                            stream.writeframes(tone + b'\0' * (gap * 4) +
                                               (tone[:-960] if truncated else tone) + b'\0' * 960)
                        if truncated:
                            with self.assertRaises(ValueError):
                                verify(path, repetitions=2)
                        else:
                            result = verify(path, repetitions=2)
                            self.assertEqual([m['start_frame'] for m in result['waveforms']], [0, 48000 + gap])
                            with self.assertRaises(ValueError):
                                verify(path)
                            with self.assertRaises(ValueError):
                                verify(path, repetitions=3)

    def test_known_tone_passes_but_silence_wrong_frequency_and_channels_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'playback.wav'
            for frequency, amplitude, different, valid in (
                    (440, 4096, False, True), (440, 0, False, False),
                    (880, 4096, False, False), (440, 4096, True, False)):
                with self.subTest(frequency=frequency, amplitude=amplitude, different=different):
                    with wave.open(str(path), 'wb') as stream:
                        stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                        samples = (int(amplitude * math.sin(2 * math.pi * frequency * i / 48000))
                                   for i in range(48000))
                        stream.writeframes(b''.join(struct.pack('<hh', x, -x if different else x) for x in samples))
                    if valid:
                        self.assertEqual(verify(path)['status'], 'verified')
                    else:
                        with self.assertRaises(ValueError):
                            verify(path)

    def test_short_recording_cannot_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'short.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\x00' * 400)
            with self.assertRaisesRegex(ValueError, 'duration'):
                verify(path)

    def test_leading_silence_cannot_hide_missing_tone_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'padded.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0' * (864 * 4))
                values = (int(4096 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(47760))
                stream.writeframes(b''.join(struct.pack('<hh', x, x) for x in values))
            with self.assertRaisesRegex(ValueError, 'complete uninterrupted'):
                verify(path)

    def test_complete_scaled_tone_with_backend_silence_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'complete.wav'
            with wave.open(str(path), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0' * (864 * 4))
                values = (int(0.941 * int(4096 * math.sin(2 * math.pi * 440 * i / 48000)))
                          for i in range(48000))
                stream.writeframes(b''.join(struct.pack('<hh', x, x) for x in values))
                stream.writeframes(b'\0' * 400)
            matched = verify(path)['waveform']
            self.assertEqual(matched['matched_frames'], 48000)
            self.assertEqual(matched['start_frame'], 864)
