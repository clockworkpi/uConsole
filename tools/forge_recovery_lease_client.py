"""Host-side read-only backup lease renewal over pinned recovery SSH."""
import shlex
import time

from forge_recovery_lease import clock, token


REMOTE = '''
import json,sys,time
sys.path.insert(0,"/etc/forge")
from forge_ram_identity import verify
from forge_recovery_lease_watchdog import observe
from forge_recovery_lease_socket import exchange
def check():
    checked=verify(observe(),expected['nonce'],expected['kernel'],expected['serial'],mode=expected['mode'])
    if checked['boot_id'] != request['boot_id']:
        raise ValueError('Recovery boot changed')
check()
receipt=exchange('/run/forge-lease/lease.sock',request)
check()
print(json.dumps(dict(receipt=receipt,now=time.monotonic())))
'''


def check_receipt(result, request, previous=None):
    if type(result) is not dict or set(result) != {'receipt', 'now'}:
        raise ValueError('Unexpected lease reply')
    receipt = result['receipt']
    fields = {'schema', 'purpose', 'nonce', 'boot_id', 'owner', 'sequence',
              'deadline_monotonic', 'hard_deadline_monotonic',
              'root_write_authorized', 'normal_boot_release_authorized'}
    if type(receipt) is not dict or set(receipt) != fields:
        raise ValueError('Unexpected lease receipt fields')
    for field in ('schema', 'purpose', 'nonce', 'boot_id', 'owner', 'sequence'):
        if type(receipt[field]) is not type(request[field]) or receipt[field] != request[field]:
            raise ValueError('Lease receipt is not bound to the request')
    if receipt['root_write_authorized'] is not False or receipt['normal_boot_release_authorized'] is not False:
        raise ValueError('Backup lease cannot confer write or release authority')
    now = clock(result['now'])
    deadline = clock(receipt['deadline_monotonic'])
    hard = clock(receipt['hard_deadline_monotonic'])
    if not now < deadline <= min(now + 300, hard) or hard > now + 86400:
        raise ValueError('Unbounded or expired lease receipt')
    if previous is not None and (hard != previous['hard_deadline_monotonic'] or
                                 deadline < previous['deadline_monotonic']):
        raise ValueError('Recovery lease deadline changed inconsistently')
    return dict(receipt)


class BackupLeaseClient:
    def __init__(self, probe, boot_id, owner):
        token(owner, '[0-9a-f]{64}')
        probe.inspect(expected_boot_id=boot_id)
        self.probe, self.boot_id, self.owner = probe, boot_id, owner
        self.pending = None
        self.accepted = None

    def renew(self, seconds=300):
        if type(seconds) is not int or not 1 <= seconds <= 300:
            raise ValueError('Invalid backup lease duration')
        if self.pending is not None and self.pending['seconds'] != seconds:
            raise ValueError('Resolve the pending renewal before changing its duration')
        if self.pending is None:
            sequence = 1 if self.accepted is None else self.accepted['sequence'] + 1
            self.pending = dict(schema=1, purpose='offline-backup', nonce=self.probe.nonce,
                                boot_id=self.boot_id, owner=self.owner, sequence=sequence, seconds=seconds)
        expected = dict(nonce=self.probe.nonce, kernel=self.probe.kernel,
                        serial=self.probe.serial, mode=self.probe.mode)
        source = 'expected=' + repr(expected) + '\nrequest=' + repr(self.pending) + '\n' + REMOTE
        # Errors preserve exactly the same request for an explicit retry. Never
        # manufacture a new sequence because an SSH reply was lost.
        result = self.probe._observe('/usr/bin/python3 -I -S -c ' + shlex.quote(source))
        receipt = check_receipt(result, self.pending, self.accepted)
        self.probe.inspect(expected_boot_id=self.boot_id)
        self.accepted, self.pending = receipt, None
        return dict(receipt)


class LeasePulse:
    """Bounded synchronous renewal for streaming and archive verification."""
    def __init__(self, client, *, now=time.monotonic):
        self.client, self.now = client, now
        self.previous = clock(now())
        self.due = self.previous

    def __call__(self):
        sampled = clock(self.now())
        if sampled < self.previous:
            raise ValueError('Host lease clock reversed')
        self.previous = sampled
        if sampled >= self.due:
            self.client.renew()
            # Count interval from request dispatch, not a potentially slow reply.
            self.due = sampled + 60
