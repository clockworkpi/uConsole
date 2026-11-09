"""Durable single-owner recovery lease sessions for foreground controller jobs.

No background keeper, disk write permission, reboot, or automatic retry. A
pending renewal survives restart and requires an explicit retry of its exact
request. The lock spans the entire caller-owned job, not just a renewal RPC.
"""
import copy
import fcntl
import os
from pathlib import Path

from forge_recovery_bootplan import digest
from forge_recovery_commit_protocol import exact
from forge_recovery_lease import token
from forge_recovery_lease_client import BackupLeaseClient, check_receipt
from forge_target_journal import private_directory, read_record, write_record


def binding(probe, boot_id, owner):
    token(probe.nonce, '[0-9a-f]{32}')
    token(boot_id, '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
    token(owner, '[0-9a-f]{64}')
    if probe.mode not in ('physical', 'emulated'):
        raise ValueError('Recovery session requires an explicit verified target mode')
    return dict(schema=1, host=probe.host, port=probe.port, nonce=probe.nonce,
                kernel=probe.kernel, serial=probe.serial, mode=probe.mode,
                boot_id=boot_id, owner=owner, key_sha256=probe._pins[0],
                known_hosts_sha256=probe._pins[1], root_write_authorized=False,
                normal_boot_release_authorized=False)


def prepare(directory, probe, boot_id, owner):
    """Enroll an unrenewed boot lease locally; no target request is sent.

    The first request is sequence one. The target refuses out-of-order or
    expired requests; an exact duplicate can only acknowledge its old deadline.
    Never query, guess, or advance to an existing target's current sequence.
    """
    value = binding(probe, boot_id, owner)
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        pin = write_record(fd, 'binding.json', value)
    finally:
        os.close(fd)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return dict(status='prepared-not-contacted', journal=str(directory), binding_sha256=pin,
                root_write_authorized=False, normal_boot_release_authorized=False)


class Session:
    def __init__(self, directory, pin, probe, boot_id, owner):
        # Unlike the transient client, opening retained evidence is local. The
        # transient client's identity checks run only within a locked renewal.
        self.probe, self.boot_id, self.owner = probe, boot_id, owner
        self.pending = self.accepted = None
        self.fd = None
        self.poisoned = False
        self.unresolved = False
        fd = private_directory(directory)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            saved = read_record(fd, 'binding.json')
            if digest(saved) != pin or not exact(saved, binding(probe, boot_id, owner)):
                raise ValueError('Recovery session differs from approved target binding')
            names = set(os.listdir(fd)) - {'binding.json'}
            sequence = 1
            while names:
                intent, reply = self.names(sequence)
                if intent not in names:
                    raise ValueError('Recovery lease journal has a gap or unexpected record')
                request = read_record(fd, intent)
                expected = self.request(sequence, request.get('seconds'))
                if not exact(request, expected):
                    raise ValueError('Retained recovery lease request differs')
                names.remove(intent)
                if reply not in names:
                    if names:
                        raise ValueError('Renewal followed an unresolved recovery lease request')
                    self.pending, self.unresolved = expected, True
                    break
                result = read_record(fd, reply)
                self.accepted = check_receipt(result, request, self.accepted)
                names.remove(reply)
                sequence += 1
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def names(sequence):
        return (f'renewal-{sequence:016d}-intent.json', f'renewal-{sequence:016d}-reply.json')

    def request(self, sequence, seconds):
        if type(seconds) is not int or not 1 <= seconds <= 300:
            raise ValueError('Invalid retained recovery lease duration')
        return dict(schema=1, purpose='offline-backup', nonce=self.probe.nonce,
                    boot_id=self.boot_id, owner=self.owner, sequence=sequence, seconds=seconds)

    def renew(self, seconds=300):
        if self.unresolved:
            raise RuntimeError('Pending recovery renewal requires explicit retry_pending()')
        return self._renew(seconds, retry=False)

    def retry_pending(self):
        if not self.unresolved or self.pending is None:
            raise ValueError('No pending recovery lease request to retry')
        return self._renew(self.pending['seconds'], retry=True)

    def _renew(self, seconds, *, retry):
        if self.fd is None or self.poisoned:
            raise RuntimeError('Recovery lease session is closed or its journal failed')
        if not retry:
            sequence = 1 if self.accepted is None else self.accepted['sequence'] + 1
            request = self.request(sequence, seconds)
            try:
                write_record(self.fd, self.names(sequence)[0], request)
            except BaseException:
                self.poisoned = True
                raise
            self.pending = request
        request = copy.deepcopy(self.pending)
        # Capture the actual target timestamp too: reopening validates historical
        # receipts, never fabricates a current clock or calls an old lease live.
        observed = []
        class CaptureProbe:
            def __getattr__(_, name):
                return getattr(original, name)
            def _observe(_, command):
                result = original._observe(command)
                observed.append(copy.deepcopy(result))
                return result
        original = self.probe
        self.unresolved = True
        client = BackupLeaseClient(CaptureProbe(), self.boot_id, self.owner)
        client.pending, client.accepted = copy.deepcopy((self.pending, self.accepted))
        receipt = client.renew(seconds)
        if len(observed) != 1:
            raise ValueError('Expected exactly one recovery renewal reply')
        try:
            write_record(self.fd, self.names(request['sequence'])[1], observed[0])
        except BaseException:
            self.poisoned = True
            raise
        self.accepted, self.pending = copy.deepcopy(client.accepted), None
        self.unresolved = False
        return receipt

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
