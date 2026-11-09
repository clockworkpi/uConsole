import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from workbench_schematic_trace import Playback, Recording, validate


class TraceTests(unittest.TestCase):
    def recording(self):
        recording = Recording('owned-test', 100.)
        for stamp, state in ((100., 'present'), (101., 'active'), (102., 'present')):
            recording.append(dict(identity='owned-test', component='keyboard', state=state,
                                  time=stamp, source='test counter', detail='test observation'))
        return recording

    def test_roundtrip_deterministic_backward_seek_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'trace.json'
            recording = self.recording()
            recording.save(path)
            with self.assertRaises(FileExistsError):
                recording.save(path)
            replay = Playback.load(path)
            self.assertEqual(replay.duration, 2.)
            for stamp, state in ((1.2, 'active'), (2., 'present'), (0., 'present'), (1.2, 'active'), (6., 'stale')):
                self.assertEqual(replay.seek(stamp).view('keyboard', stamp)['state'], state)

    def test_rejects_invalid_identity_order_schema_fields(self):
        data = self.recording().data
        for mutate in (lambda d: d.update(schema='no'),
                       lambda d: d['events'][1].update(time=-1),
                       lambda d: d['events'][1].update(time=float('nan')),
                       lambda d: d['events'][1].update(identity='another-vm'),
                       lambda d: d['events'][1].update(component='unknown-component'),
                       lambda d: d['events'][1].update(extra='arbitrary-payload'),
                       lambda d: d['events'].reverse()):
            invalid = copy.deepcopy(data)
            mutate(invalid)
            with self.assertRaises(ValueError):
                validate(invalid)

    def test_recording_bounds_and_drops_other_runtime(self):
        recording = self.recording()
        event = dict(recording.data['events'][0], time=103.)
        with patch('workbench_schematic_trace.MAX_EVENTS', 3):
            self.assertFalse(recording.append(event))
        self.assertTrue(recording.full)
        self.assertEqual(recording.data['dropped'], 1)
        self.assertFalse(recording.append(dict(event, identity='another')))
        self.assertEqual(len(recording.data['events']), 3)

    def test_large_trace_seek_uses_per_component_index(self):
        recording = Recording('owned-test', 0.)
        for stamp in range(10000):
            self.assertTrue(recording.append(dict(identity='owned-test', component='storage', state='active',
                                                time=float(stamp), source='counter', detail='bytes')))
        replay = Playback(recording.data)
        self.assertEqual(len(replay.seek(9999.).values), 1)
        self.assertEqual(replay.seek(5000.).values['storage']['time'], 5000.)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'POSIX FIFO test')
    def test_replay_rejects_fifo_without_blocking_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'not-a-recording'
            os.mkfifo(path)
            with self.assertRaisesRegex(ValueError, 'regular file'):
                Playback.load(path)


if __name__ == '__main__':
    unittest.main()
