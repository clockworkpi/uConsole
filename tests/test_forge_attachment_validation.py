"""Peer acceptance must prove policy denial, not merely any error response."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from validate_forge_attachment_gui import verify_peer


class AttachmentValidationTests(unittest.TestCase):
    def replies(self):
        return [{'id': 1, 'result': {}},
                {'id': 2, 'result': {'structuredContent': {'status': 'running'}}},
                {'id': 3, 'result': {'isError': True, 'content': [
                    {'text': 'Clients may cancel only their own submitted jobs'}]}},
                {'id': 4, 'result': {'isError': True, 'content': [
                    {'text': 'Wait for the attached agent job to finish'}]}}]

    def test_running_observation_and_exact_policy_denials_pass(self):
        verify_peer(self.replies())

    def test_unrelated_errors_do_not_prove_policy(self):
        for index in (2, 3):
            replies = self.replies()
            replies[index]['result']['content'][0]['text'] = 'Runtime not available'
            with self.assertRaisesRegex(ValueError, 'policy'):
                verify_peer(replies)

    def test_terminal_job_or_successful_peer_mutation_fails(self):
        replies = self.replies()
        replies[1]['result']['structuredContent']['status'] = 'completed'
        with self.assertRaisesRegex(ValueError, 'running'):
            verify_peer(replies)
        for index in (2, 3):
            replies = self.replies()
            replies[index]['result']['isError'] = False
            with self.assertRaisesRegex(ValueError, 'policy'):
                verify_peer(replies)


if __name__ == '__main__':
    unittest.main()
