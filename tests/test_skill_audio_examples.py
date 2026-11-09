"""Keep the agent skill's callable examples compatible with the real schema."""
import json
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from uconsole_mcp import BY_NAME, validate


class SkillAudioExamples(unittest.TestCase):
    def test_examples_validate_against_exposed_tool_contracts(self):
        reference = (ROOT / 'skills/uconsole-forge/references/audio.md').read_text()
        examples = re.findall(r'```json\n(.*?)\n```', reference, re.S)
        self.assertTrue(examples, 'Audio reference must have executable tool examples')
        for source in examples:
            call = json.loads(source)
            with self.subTest(tool=call['name'], arguments=call['arguments']):
                validate(BY_NAME[call['name']]['inputSchema'], call['arguments'])
