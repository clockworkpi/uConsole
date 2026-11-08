"""Keep every GENET peer at real8GiB without Linux swap reservation."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import test_emulator_genet_network as genet


class GenetMemoryArguments(unittest.TestCase):
    def command(self, platform):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            process = MagicMock()
            process.poll.return_value = 0
            channel = MagicMock()
            channel.__enter__.return_value = channel
            with patch.object(sys, 'platform', platform), \
                    patch.object(genet.subprocess, 'Popen', return_value=process) as launch, \
                    patch.object(genet, 'connect', return_value=channel):
                genet.Machine(Path(directory), 'peer', stack, '/private/qemu', incoming=True)
                return launch.call_args.args[0]

    def test_linux_peer_retains_eight_gib_without_swap_reservation(self):
        command = self.command('linux')
        self.assertEqual(command[command.index('-M') + 1],
                         'raspi4b-8g,memory-backend=test-memory')
        self.assertEqual(command[command.index('-object') + 1],
                         'memory-backend-ram,id=test-memory,size=8G,reserve=off')
        self.assertEqual(command[-2:], ['-incoming', 'defer'])

    def test_darwin_peer_retains_eight_gib_without_unsupported_property(self):
        command = self.command('darwin')
        self.assertEqual(command[command.index('-M') + 1],
                         'raspi4b-8g,memory-backend=test-memory')
        self.assertEqual(command[command.index('-object') + 1],
                         'memory-backend-ram,id=test-memory,size=8G')
        self.assertNotIn('reserve=', ' '.join(command))


if __name__ == '__main__':
    unittest.main()
