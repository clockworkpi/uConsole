import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import forge_target_dependencies as graph
from forge_target_services import DEPENDENCIES
if __package__:
    from .target_test_support import identity
else:
    from target_test_support import identity


class DependencyGraphTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(graph, 'identity', side_effect=identity))
        self.nodes = {name: {key: [] for key in DEPENDENCIES}
                      for name in ('proof.service', 'parent.target', 'child.socket')}
        self.nodes['proof.service']['Requires'] = ['parent.target']
        self.nodes['parent.target']['Wants'] = ['child.socket']
        self.nodes['child.socket']['Before'] = ['proof.service']

    def capture(self, **kwargs):
        with patch.object(graph, 'observe', side_effect=lambda name, timeout: copy.deepcopy(self.nodes[name])):
            return graph.capture_local(['proof.service'], **kwargs)

    def test_closure_handles_cycles_and_checks_each_node_twice(self):
        with patch.object(graph, 'observe', side_effect=lambda name, timeout: copy.deepcopy(self.nodes[name])) as query:
            result = graph.capture_local(['proof.service'])
        self.assertEqual(query.call_count, 6)
        self.assertEqual(graph.verify(result, ['proof.service'])['nodes'], self.nodes)
        self.assertFalse(result['execution_approved'])

    def test_limit_refuses_partial_graph(self):
        with self.assertRaisesRegex(ValueError, 'unit limit'):
            self.capture(max_units=2)

    def test_changed_second_pass_refuses_success(self):
        calls = []
        def query(name, timeout):
            calls.append(name)
            result = copy.deepcopy(self.nodes[name])
            if len(calls) > 3:
                result['Wants'] = ['new.service']
            return result
        with patch.object(graph, 'observe', side_effect=query):
            with self.assertRaisesRegex(ValueError, 'changed during'):
                graph.capture_local(['proof.service'])

    def test_boot_change_refuses_success(self):
        first = graph.identity()
        with patch.object(graph, 'identity', side_effect=[first, dict(first, boot_id='changed')]):
            with self.assertRaisesRegex(ValueError, 'boot changed'):
                self.capture()

    def test_deadline_refuses_query_before_starting_it(self):
        with patch.object(graph.time, 'monotonic', side_effect=[0, 2]), patch.object(graph, 'observe') as query:
            with self.assertRaises(TimeoutError):
                graph.capture_local(['proof.service'], timeout=1)
            query.assert_not_called()

    def test_verifier_rejects_missing_edge_and_unreachable_extra_node(self):
        record = self.capture()
        del record['nodes']['child.socket']
        with self.assertRaisesRegex(ValueError, 'uncaptured edge'):
            graph.verify(record, ['proof.service'])
        record = self.capture()
        record['nodes']['extra.service'] = {key: [] for key in DEPENDENCIES}
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            graph.verify(record, ['proof.service'])

    def test_capture_does_not_implicitly_allow_templates_or_options_as_roots(self):
        for roots in (['--help'], ['foo@bar.service'], ['*.service']):
            with patch.object(graph, 'observe') as query, self.assertRaises(ValueError):
                graph.capture_local(roots)
            query.assert_not_called()
