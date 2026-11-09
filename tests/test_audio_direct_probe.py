import ctypes.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from audio_direct_probe import DIRECT_ALSA


class DirectAudioProbeTests(unittest.TestCase):
    def test_guest_source_compiles(self):
        compile(DIRECT_ALSA, '<direct-alsa-guest>', 'exec')

    @unittest.skipUnless(ctypes.util.find_library('asound'), 'needs libasound null PCM')
    def test_real_library_null_pcm_write_and_drain(self):
        namespace = {}
        exec(DIRECT_ALSA, namespace)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                stream.writeframes(b'\0' * 192000)
            result = namespace['play_direct'](source, 'null')
            details = json.loads(result.stdout)
            self.assertEqual(details['written_frames'], 48000)
            self.assertEqual(details['drain_exit_code'], 0)
            # A truncated WAV header must not result in an out-of-bounds
            # ctypes buffer read by the C audio library.
            with source.open('r+b') as stream:
                stream.truncate(source.stat().st_size - 4)
            with self.assertRaisesRegex(ValueError, 'complete input frames'):
                namespace['play_direct'](source, 'null')
