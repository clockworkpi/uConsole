"""Recorder-driving mechanics only; these tests do not qualify a desktop."""
import json
from pathlib import Path
import queue
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import drive_forge_desktop as driver
from drive_forge_desktop import await_event, scenario


class DesktopDriverTests(unittest.TestCase):
    def load(self, steps, **extra):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scenario.json'
            path.write_text(json.dumps({'schema': 1, 'steps': steps, **extra}))
            return scenario(path)

    def test_valid(self):
        steps = [{'at_seconds': 0, 'request': {'action': 'status'}},
                 {'at_seconds': 30, 'request': {'action': 'capture'}}]
        self.assertEqual(self.load(steps), steps)

    def test_repository_scenarios(self):
        directory = Path(__file__).resolve().parents[1] / 'docs/scenarios'
        capture = scenario(directory / 'factory-desktop-capture.json')
        onboarding = scenario(directory / 'factory-desktop-onboarding.json')
        self.assertEqual(capture[-1]['request']['action'], 'capture')
        self.assertEqual(onboarding[-1]['request']['action'], 'finish')
        self.assertEqual(sum(step['request']['action'] == 'type-secret'
                             for step in onboarding), 2)
        self.assertTrue(any(step['request'].get('text') == 'sudo reboot'
                            for step in onboarding))
        self.assertTrue(any(step['request'].get('text') == 'sudo poweroff'
                            for step in onboarding))

    def test_invalid(self):
        for steps in ([], [{'at_seconds': True, 'request': {'action': 'capture'}}],
                      [{'at_seconds': 1501, 'request': {'action': 'capture'}}],
                      [{'at_seconds': 0, 'request': {'action': 'shell'}}],
                      [{'at_seconds': 0, 'request': {'action': 'finish'}},
                       {'at_seconds': 0, 'request': {'action': 'status'}}],
                      [{'at_seconds': 2, 'request': {'action': 'status'}},
                       {'at_seconds': 1, 'request': {'action': 'capture'}}]):
            with self.subTest(steps=steps), self.assertRaises(ValueError):
                self.load(steps)
        with self.assertRaises(ValueError):
            self.load([{'at_seconds': 0, 'request': {'action': 'status'}}], extra=True)
        with self.assertRaises(ValueError):
            self.load([{'at_seconds': 0, 'request': {'action': 'status'}}], schema=True)

    def test_response(self):
        inbox = queue.Queue()
        inbox.put({'action': 'status'})
        inbox.put({'action': 'capture', 'path': 'screenshot.png'})
        self.assertEqual(await_event(inbox, 'capture', time.monotonic() + 1)['path'], 'screenshot.png')

    def test_eof_error_and_timeout(self):
        for event in (None, {'action': 'capture', 'error': 'guest stopped'}):
            inbox = queue.Queue()
            inbox.put(event)
            with self.assertRaises(RuntimeError):
                await_event(inbox, 'capture', time.monotonic() + 1)
        with self.assertRaises(TimeoutError):
            await_event(queue.Queue(), 'capture', time.monotonic() - 1)

    def test_child_protocol_and_retained_failure(self):
        # Real pipes and subprocess teardown, with a tiny synthetic recorder.
        # No guest is booted, and even the successful receipt must not say pass.
        child = '''
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]) / 'desktop-record-fixture'
root.mkdir()
print(json.dumps({'action': 'ready', 'evidence_directory': str(root)}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request['action'] == 'finish':
        break
    if sys.argv[2] == 'fail':
        print(json.dumps({'action': request['action'], 'error': 'fixture failure'}), flush=True)
    else:
        print(json.dumps({'action': request['action']}), flush=True)
(root / 'record.json').write_text(json.dumps({
    'status': 'recorded' if sys.argv[2] == 'finish' else 'interrupted',
    'forced_cleanup': sys.argv[2] != 'finish'}))
'''
        real_popen = driver.subprocess.Popen
        for mode in ('ok', 'fail', 'finish'):
            fail = mode == 'fail'
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                recipe = root / 'scenario.json'
                recipe.write_text(json.dumps({'schema': 1, 'steps': [
                    {'at_seconds': 0, 'request': {'action': 'finish' if mode == 'finish' else 'status'}}]}))
                output = root / 'output'
                def launch(command, **kwargs):
                    self.assertTrue(command[1].endswith('record_forge_desktop.py'))
                    return real_popen([sys.executable, '-u', '-c', child, str(root), mode], **kwargs)
                with patch.object(driver.subprocess, 'Popen', side_effect=launch), \
                        patch.object(sys, 'argv', ['driver', '--workspace', str(root),
                                                  '--scenario', str(recipe), '--output', str(output)]):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, 'fixture failure'):
                            driver.main()
                    else:
                        driver.main()
                result = json.loads((output / 'driver.json').read_text())
                self.assertFalse(result['automated_desktop_pass'])
                self.assertEqual(result['status'], 'failed' if fail else 'scenario-recorded')
                if mode == 'finish':
                    self.assertEqual(result['recorder_status'], 'recorded')
                    self.assertFalse(result['forced_cleanup'])
                self.assertTrue((root / 'desktop-record-fixture/record.json').exists())
