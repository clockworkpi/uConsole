import json
import multiprocessing
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from forge_recovery_lease import Lease
from forge_recovery_lease_timer import RenewalService, SoftwareTimer


def timer_process(directory, initial, channel):
    current = [initial.started]
    timer = SoftwareTimer(directory, initial, lambda: channel.send(('expired', current[0])),
                          now=lambda: current[0])
    try:
        channel.send(('ready', None))
        while True:
            current[0] = channel.recv()
            try:
                channel.send(('live', timer.step()))
            except RuntimeError:
                channel.send(('terminal', None))
                return
    finally:
        timer.close()
        channel.close()


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux lease socket required')
class LeaseTimerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name).resolve()
        self.time = 10
        self.expired = []
        self.initial = Lease.start('a'*32, '11111111-2222-3333-4444-555555555555', 'b'*64, 10)
        self.service = RenewalService(self.path, self.initial, now=lambda: self.time)
        self.addCleanup(self.service.close)
        self.timer = SoftwareTimer(self.path, self.initial,
                                   lambda: self.expired.append(self.time), now=lambda: self.time)
        self.addCleanup(self.timer.close)
        self.request = dict(schema=1, purpose='offline-backup', nonce=self.initial.nonce,
                            boot_id=self.initial.boot_id, owner=self.initial.owner,
                            sequence=1, seconds=300)

    def renew(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
            client.settimeout(2)
            client.connect(str(self.path/'lease.sock'))
            client.send(json.dumps(self.request).encode())
            self.service.poll()
            return json.loads(client.recv(4096))

    def test_renewal_reaches_independent_timer_and_loss_expires(self):
        self.assertEqual(self.timer.step(), 0.2)
        self.time = 200
        self.assertEqual(self.renew()['deadline_monotonic'], 500)
        self.assertEqual(self.timer.step(), 0.2)
        self.service.close()  # Networking/renewal process disappears.
        self.time = 499.9
        self.assertAlmostEqual(self.timer.step(), 0.1)
        self.time = 500
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [500])
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [500])

    def test_retry_does_not_delay_expiry(self):
        self.time = 100
        self.renew()
        self.timer.step()
        self.time = 399
        self.renew()
        self.timer.step()
        self.time = 400
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [400])

    def test_missing_corrupt_and_wrong_mode_state_expire(self):
        (self.path/'deadline.json').write_text('{broken')
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [10])

    def test_idle_clock_reversal_is_not_hidden_by_old_snapshot(self):
        self.time = 100
        self.timer.step()
        self.time = 99
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [99])

    def test_wait_failure_expires(self):
        def fail(_):
            raise OSError('clock wait failed')
        with self.assertRaises(RuntimeError):
            self.timer.run(wait=fail)
        self.assertEqual(self.expired, [10])

    def test_slow_read_cannot_run_past_expiry(self):
        samples = iter([309, 310])
        self.timer.now = lambda: next(samples)
        with self.assertRaises(RuntimeError):
            self.timer.step()
        self.assertEqual(self.expired, [10])

    def test_renewal_published_during_read_is_not_clock_reversal(self):
        renewed=self.initial.renew(self.request,200)
        original=self.timer.reader._read
        def reading(*args,**kwargs):
            self.service.writer.publish(renewed,200)
            return original(*args,**kwargs)
        samples=iter([100,201,202])
        self.timer.now=lambda:next(samples)
        with patch.object(self.timer.reader,'_read',side_effect=reading):
            self.assertEqual(self.timer.step(),0.2)
        self.assertEqual(self.expired,[])
        self.assertEqual(self.timer.reader.state,renewed)

    def test_post_read_reversal_and_future_publication_still_expire(self):
        for finished,publish in ((99,False),(150,True)):
            with self.subTest(finished=finished):
                timer=SoftwareTimer(self.path,self.initial,
                    lambda:self.expired.append('expired'),now=lambda:10)
                try:
                    original=timer.reader._read
                    def reading(*args,**kwargs):
                        if publish:
                            self.service.writer.publish(self.initial.renew(self.request,200),200)
                        return original(*args,**kwargs)
                    samples=iter([100,finished])
                    timer.now=lambda:next(samples)
                    with patch.object(timer.reader,'_read',side_effect=reading),self.assertRaises(RuntimeError):
                        timer.step()
                    self.assertTrue(timer.terminal)
                finally: timer.close()
        self.assertEqual(self.expired,['expired','expired'])

    def test_reversal_after_snapshot_validation_also_expires(self):
        samples=iter([100,101,100.5])
        self.timer.now=lambda:next(samples)
        with self.assertRaises(RuntimeError): self.timer.step()
        self.assertEqual(self.expired,[10])

    def test_restart_cannot_reset_deadline(self):
        with self.assertRaises(FileExistsError):
            RenewalService(self.path, self.initial, now=lambda: self.time)
        self.assertEqual(self.timer.step(), 0.2)

    def test_separate_process_reads_renewal_then_expires_without_service(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        process = context.Process(target=timer_process, args=(self.path, self.initial, child))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv()[0], 'ready')
            self.time = 200
            self.renew()
            parent.send(201)
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv()[0], 'live')
            self.service.close()
            parent.send(500)
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv(), ('expired', 500))
            self.assertTrue(parent.poll(5))
            self.assertEqual(parent.recv()[0], 'terminal')
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(5)
            process.close()
