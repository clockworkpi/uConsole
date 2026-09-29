import os
import tempfile
import time
import unittest
from unittest.mock import patch

from forge_recovery_restore_io import PipeIO, BLOCK
from forge_recovery_restore_protocol import header, write_header
import test_recovery_restore_protocol


class RestorePipeTests(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.host_write = os.pipe()
        self.host_read, self.write_fd = os.pipe()
        for fd in (self.read_fd, self.host_write, self.host_read, self.write_fd):
            self.addCleanup(self.close_fd, fd)

    @staticmethod
    def close_fd(fd):
        try: os.close(fd)
        except OSError: pass

    def channel(self, **kwargs):
        channel = PipeIO(self.read_fd, self.write_fd, **kwargs)
        self.addCleanup(channel.close)
        return channel

    def test_header_prefetch_preserves_binary_body_and_next_frame(self):
        os.write(self.host_write, b'{"type":"chunk"}\n\x00\xff\nbody\n{"type":"end"}\n')
        channel = self.channel()
        self.assertEqual(header(channel), {'type': 'chunk'})
        self.assertEqual(channel.read(8), b'\x00\xff\nbody\n')
        self.assertEqual(header(channel), {'type': 'end'})
        write_header(channel, {'type': 'ack'})
        channel.flush()
        self.assertEqual(os.read(self.host_read, 100), b'{"type": "ack"}\n')

    def test_idle_header_timeout_is_terminal(self):
        channel = self.channel(idle_timeout=0.02)
        start = time.monotonic()
        with self.assertRaises(TimeoutError): header(channel)
        self.assertLess(time.monotonic()-start, 2)
        os.write(self.host_write, b'{}\n')
        with self.assertRaises(RuntimeError): header(channel)

    def test_partial_header_then_stall_does_not_wait_forever(self):
        os.write(self.host_write, b'{"partial":')
        channel = self.channel(idle_timeout=0.02)
        with self.assertRaises(TimeoutError): header(channel)
        self.assertTrue(channel.failed)

    def test_body_read_and_final_eof_have_deadlines(self):
        channel = self.channel(idle_timeout=0.02)
        os.write(self.host_write, b'x')
        self.assertEqual(channel.read(1), b'x')
        with self.assertRaises(TimeoutError): channel.read(1)

    def test_total_deadline_is_not_extended_by_available_input(self):
        with patch('forge_recovery_restore_io.time.monotonic', return_value=10) as clock:
            channel = self.channel(timeout=1, idle_timeout=1)
            os.write(self.host_write, b'xyz')
            clock.return_value = 10.1
            self.assertEqual(channel.read(1), b'x')
            clock.return_value = 10.9
            self.assertEqual(channel.read(1), b'y')
            clock.return_value = 11
            with self.assertRaises(TimeoutError): channel.read(1)
            with self.assertRaises(RuntimeError): channel.read(1)

    def test_long_local_hash_does_not_consume_next_operation_idle_budget(self):
        with patch('forge_recovery_restore_io.time.monotonic', return_value=10) as clock:
            channel = self.channel(timeout=500, idle_timeout=90)
            clock.return_value = 200
            os.write(self.host_write, b'x')
            self.assertEqual(channel.read(1), b'x')

    def test_blocked_output_times_out_and_never_retries(self):
        channel = self.channel(idle_timeout=0.02)
        while True:
            try: os.write(self.write_fd, b'x'*4096)
            except BlockingIOError: break
        with self.assertRaises(TimeoutError): channel.write(b'ack\n')
        os.read(self.host_read, 4096)
        with self.assertRaises(RuntimeError): channel.write(b'ack\n')

    def test_disconnect_still_allows_completion_output(self):
        channel = self.channel()
        os.close(self.host_write)
        self.assertEqual(channel.read(1), b'')
        self.assertEqual(channel.readline(10), b'')
        self.assertEqual(channel.write(b'complete\n'), 9)
        self.assertEqual(os.read(self.host_read, 100), b'complete\n')

    def test_broken_output_is_terminal(self):
        channel = self.channel()
        os.close(self.host_read)
        with self.assertRaises(BrokenPipeError): channel.write(b'ack\n')
        self.assertTrue(channel.failed)

    def test_bounded_header_rejection_and_read_bounds(self):
        os.write(self.host_write, b'x'*4097)
        channel = self.channel()
        with self.assertRaises(ValueError): header(channel)
        with self.assertRaises(ValueError): channel.read(BLOCK+1)
        self.assertTrue(channel.failed)

    def test_context_restores_flags_without_closing_descriptors(self):
        with self.channel() as channel:
            self.assertFalse(os.get_blocking(self.read_fd))
            self.assertFalse(os.get_blocking(self.write_fd))
        self.assertTrue(os.get_blocking(self.read_fd))
        self.assertTrue(os.get_blocking(self.write_fd))
        with self.assertRaises(RuntimeError): channel.flush()
        os.fstat(self.read_fd)

    def test_bad_deadlines_and_regular_files_rejected_without_flag_changes(self):
        for value in (True, 0, -1, float('nan'), float('inf'), 82801):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PipeIO(self.read_fd, self.write_fd, timeout=value)
        with tempfile.TemporaryFile() as stream, self.assertRaises(ValueError):
            PipeIO(stream.fileno(), self.write_fd)
        self.assertTrue(os.get_blocking(self.read_fd))
        self.assertTrue(os.get_blocking(self.write_fd))

    def test_real_child_receiver_uses_bounded_pipes_for_complete_source(self):
        fixture = self.receiver_fixture()
        result = fixture.process(fixture.wire())
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'"type": "root-verified"', result.stdout)
        self.assertEqual(fixture.path.read_bytes(), fixture.fixture.root_bytes)

    def test_real_child_disconnect_preserves_partial_root_without_completion(self):
        fixture = self.receiver_fixture()
        wire = fixture.wire()
        end = wire.index(b'\n')+1+fixture.manifest['chunks'][0]['bytes']
        result = fixture.process(wire[:end])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'"type": "chunk-verified"', result.stdout)
        self.assertNotIn(b'"type": "root-verified"', result.stdout)
        self.assertEqual(fixture.path.read_bytes(), fixture.fixture.root_bytes[:-512]+b'0'*512)

    def receiver_fixture(self):
        fixture = test_recovery_restore_protocol.RestoreProtocolTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.CHILD = fixture.CHILD.replace(
            'from forge_recovery_restore_protocol import receive',
            'from forge_recovery_restore_protocol import receive\n'
            'from forge_recovery_restore_io import PipeIO\n'
            'channel=PipeIO(sys.stdin.fileno(),sys.stdout.fileno(),timeout=20,idle_timeout=2)')
        fixture.CHILD = fixture.CHILD.replace(
            'def ack(value): print(json.dumps(value),flush=True)',
            'def ack(value):\n'
            '        from forge_recovery_restore_protocol import write_header\n'
            '        write_header(channel,value)')
        fixture.CHILD = fixture.CHILD.replace('receive(sys.stdin.buffer,', 'receive(channel,')
        # Completion uses the same bounded descriptor, never buffered stdout.
        fixture.CHILD = fixture.CHILD.replace('print(json.dumps(result),flush=True)', 'ack(result)')
        return fixture


if __name__ == '__main__': unittest.main()
