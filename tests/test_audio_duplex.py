"""Two-device mode retains per-device evidence and reconciles partial effects."""
import json
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from forge_audio import devices, operation, query, MODES
from uconsole_mcp import BY_NAME, validate
from duplex_audio_probe import source, verified_evidence


class DuplexControls(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.specs = {item['id']: item for item in devices('usb-duplex')}
        self.present = {name: True for name in self.specs}
        self.mutations = []
        self.runtime = SimpleNamespace(args=SimpleNamespace(audio='usb-duplex'),
                                       identity='owned', control=self.control)

    def control(self, name, arguments):
        if name == 'qom-list':
            return [{'name': key, 'type': 'child<' + self.specs[key]['driver'] + '>'}
                    for key, present in self.present.items() if present]
        if name == 'qom-get':
            return self.present[arguments['path'].rsplit('/', 1)[-1]]
        self.mutations.append((name, arguments))
        self.present[arguments['id']] = name == 'device_add'

    def test_modes_agree_with_mcp_and_default_has_no_devices(self):
        self.assertEqual(tuple(BY_NAME['boot']['inputSchema']['properties']['audio']['enum']), MODES)
        validate(BY_NAME['boot']['inputSchema'], {'workspace': 'main', 'audio': 'usb-duplex'})
        with self.assertRaises(ValueError):
            devices('none')

    def test_both_endpoints_are_required_and_queried(self):
        state = query(self.runtime)
        self.assertTrue(state['playback'] and state['capture'] and state['connected'])
        self.assertFalse(state['host_microphone'])
        self.present['audio-capture'] = False
        state = query(self.runtime)
        self.assertFalse(state['connected'])
        self.assertTrue(state['devices']['audio-surrogate']['connected'])

    def test_both_devices_disconnect_and_reconnect_without_recreating_present_one(self):
        operation(self.runtime, self.root / 'disconnect.jsonl', False)
        self.assertFalse(any(self.present.values()))
        self.present['audio-surrogate'] = True
        self.mutations.clear()
        result = operation(self.runtime, self.root / 'connect.jsonl', True)
        self.assertTrue(result['observed']['connected'])
        self.assertEqual(self.mutations, [('device_add', self.specs['audio-capture'])])

    def test_second_device_failure_records_partial_state_without_rollback(self):
        original = self.control
        def control(name, arguments):
            if name == 'device_del' and arguments['id'] == 'audio-capture':
                raise ConnectionError('capture delete refused')
            return original(name, arguments)
        self.runtime.control = control
        path = self.root / 'partial.jsonl'
        with self.assertRaises(ConnectionError):
            operation(self.runtime, path, False)
        self.assertEqual(self.present, {'audio-surrogate': False, 'audio-capture': True})
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([(item['device'], item['state']) for item in records if 'device' in item],
                         [('audio-surrogate', 'dispatch'), ('audio-surrogate', 'acknowledged'),
                          ('audio-capture', 'dispatch')])
        record = records[-1]
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(record['rollback'])
        self.runtime.control = original
        self.mutations.clear()
        operation(self.runtime, self.root / 'reconcile.jsonl', False)
        self.assertEqual(self.mutations, [('device_del', {'id': 'audio-capture'})])

    def test_wrong_second_identity_refuses_all_mutations(self):
        self.specs['audio-capture']['driver'] = 'unrelated'
        with self.assertRaisesRegex(ValueError, 'unexpected QOM'):
            operation(self.runtime, self.root / 'wrong.jsonl', False)
        self.assertEqual(self.mutations, [])


class DuplexEvidence(unittest.TestCase):
    def test_source_is_self_contained_compilable_guest_python(self):
        compile(source(), '<duplex-guest>', 'exec')

    def test_both_data_directions_and_overlapping_running_states_required(self):
        record = {'duplex': {'playback_frames': 48000, 'capture_frames': 144000,
            'capture': {'frames': 144000, 'pattern': 'stereo-u32-frame-counter-v1'},
            'playback_card': 'card0', 'capture_card': 'card1',
            'overlap': {'playback': 'state: RUNNING\n', 'capture': 'state: RUNNING\n'}}}
        self.assertTrue(verified_evidence(record))
        for field, value in (('playback_frames', 47999), ('capture_frames', 143999),
                             ('capture', {}), ('overlap', {'playback': 'state: RUNNING\n'}),
                             ('capture_card', 'card0')):
            changed = copy.deepcopy(record)
            changed['duplex'][field] = value
            self.assertFalse(verified_evidence(changed))
        for value in (None, [], {}, {'duplex': None}):
            self.assertFalse(verified_evidence(value))
