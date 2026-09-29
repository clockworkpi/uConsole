import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from forge_recovery_lease import Lease
from forge_recovery_lease_socket import LeaseEndpoint, decode, exchange


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux peer credentials required')
class LeaseSocketTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve()/'lease.sock'
        self.time = 20
        self.published = []
        lease = Lease.start('a'*32, '11111111-2222-3333-4444-555555555555', 'b'*64, 10)
        self.server = LeaseEndpoint(self.path, lease, self.published.append, now=lambda: self.time)
        self.addCleanup(self.server.close)
        self.request = dict(schema=1, purpose='offline-backup', nonce=lease.nonce,
                            boot_id=lease.boot_id, owner=lease.owner, sequence=1, seconds=300)

    def serve(self):
        failures = []
        def run():
            try:
                self.server.poll()
            except BaseException as exc:
                failures.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 3)
        return thread, failures

    def test_real_socket_renewal_and_retry(self):
        for when in (20, 200):
            self.time = when
            thread, failures = self.serve()
            reply = exchange(self.path, self.request)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(failures)
            self.assertEqual(reply['deadline_monotonic'], 320)
            self.assertFalse(reply['root_write_authorized'])
        self.assertEqual(len(self.published), 2)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def raw(self, data):
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.settimeout(2)
            client.connect(str(self.path))
            if data is not None:
                client.send(data)
            self.server.poll()
            try:
                self.assertEqual(client.recv(4096), b'')
            except ConnectionResetError:
                pass

    def test_invalid_or_idle_clients_do_not_renew(self):
        for data in (None, b'{}', b'x'*4097, b'[]', b'{"schema":1,"schema":1}',
                     json.dumps(dict(self.request, purpose='restore')).encode()):
            with self.subTest(data=str(data)[:50]):
                self.raw(data)
                self.assertEqual(self.server.lease.sequence, 0)
        self.assertEqual(self.published, [])

    def test_wrong_peer_rejected(self):
        with patch('forge_recovery_lease_socket.peer_uid', return_value=os.geteuid()+1):
            self.raw(json.dumps(self.request).encode())
        self.assertEqual(self.published, [])

    def test_publisher_failure_poisoned_no_receipt(self):
        def fail(_):
            raise OSError('timer update failed')
        self.server.publish = fail
        thread, failures = self.serve()
        with self.assertRaises((ValueError, OSError)):
            exchange(self.path, self.request)
        thread.join(3)
        self.assertEqual(len(failures), 1)
        self.assertTrue(self.server.failed)
        with self.assertRaises(RuntimeError):
            self.server.poll()

    def test_expiry_is_terminal(self):
        self.time = 310
        with self.assertRaises(ValueError):
            self.server.poll()
        self.time = 20
        with self.assertRaises(RuntimeError):
            self.server.poll()

    def test_disconnected_client_can_retry_committed_request(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.connect(str(self.path))
            client.send(json.dumps(self.request).encode())
        self.server.poll()
        self.assertEqual(self.server.lease.sequence, 1)
        self.time = 200
        thread, failures = self.serve()
        reply = exchange(self.path, self.request)
        thread.join(3)
        self.assertFalse(failures)
        self.assertEqual(reply['deadline_monotonic'], 320)

    def test_publication_crossing_new_deadline_is_terminal(self):
        def slow(_):
            self.time = 320
        self.server.publish = slow
        thread, failures = self.serve()
        with self.assertRaises((ValueError, OSError)):
            exchange(self.path, self.request)
        thread.join(3)
        self.assertEqual(len(failures), 1)
        self.assertTrue(self.server.failed)

    def test_existing_endpoint_and_public_directory_rejected(self):
        with self.assertRaises(OSError):
            LeaseEndpoint(self.path, self.server.lease, self.published.append, now=lambda: 20)
        self.assertTrue(self.path.is_socket())
        public = self.path.parent/'public'
        public.mkdir(mode=0o755)
        with self.assertRaises(ValueError):
            LeaseEndpoint(public/'socket', self.server.lease, self.published.append, now=lambda: 20)

    def test_duplicate_json_and_nonfinite_rejected(self):
        for data in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b''):
            with self.assertRaises(ValueError):
                decode(data)
