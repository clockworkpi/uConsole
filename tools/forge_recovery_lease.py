"""Recovery lease protocol core; no transport, timer override or write authority.

The trusted receiver supplies monotonic time. Requests must arrive over the
dedicated authenticated recovery channel, never through an untrusted clock or
an image's stored credentials alone. Both timer owners must use this contract
before a production session may be extended.
"""
from dataclasses import dataclass, replace
import math
import re


def clock(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('Invalid monotonic lease time')
    return value


def token(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError('Invalid lease identity')
    return value


@dataclass(frozen=True)
class Lease:
    nonce: str
    boot_id: str
    owner: str
    started: float
    sampled: float
    deadline: float
    sequence: int = 0
    last_seconds: int = 0

    @classmethod
    def start(cls, nonce, boot_id, owner, now):
        """Start a read-only lease within an already verified RAM session.

        owner is a fresh host session token, not a bearer credential. The
        receiver must retain this instance; requests cannot replace its state.
        """
        token(nonce, '[0-9a-f]{32}')
        token(boot_id, '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
        token(owner, '[0-9a-f]{64}')
        now = clock(now)
        if not math.isfinite(now + 86400) or now + 300 <= now:
            raise ValueError('Unrepresentable lease deadline')
        return cls(nonce, boot_id, owner, now, now, now + 300)

    def check(self, now):
        now = clock(now)
        if now < self.sampled or now >= self.deadline or now >= self.started + 86400:
            raise ValueError('Recovery lease expired or clock reversed')
        return now

    def renew(self, request, now):
        """Return new immutable state; rejected requests grant no extra time.

        A duplicate of the last accepted request may acknowledge a lost reply
        but never extends the deadline. Late duplicates cannot revive expiry.
        """
        now = self.check(now)
        fields = {'schema', 'purpose', 'nonce', 'boot_id', 'owner', 'sequence', 'seconds'}
        if type(request) is not dict or set(request) != fields:
            raise ValueError('Invalid lease request fields')
        if (type(request['schema']) is not int or request['schema'] != 1 or
                request['purpose'] != 'offline-backup' or
                request['nonce'] != self.nonce or request['boot_id'] != self.boot_id or
                request['owner'] != self.owner):
            raise ValueError('Lease request is not bound to this backup session')
        seq, seconds = request['sequence'], request['seconds']
        if (type(seq) is not int or not 1 <= seq <= 2**53 - 1 or
                type(seconds) is not int or not 1 <= seconds <= 300):
            raise ValueError('Invalid lease sequence or duration')
        if seq == self.sequence and seconds == self.last_seconds:
            return replace(self, sampled=now)
        if seq != self.sequence + 1:
            raise ValueError('Replayed, conflicting or out-of-order lease request')
        deadline = min(self.started + 86400, max(self.deadline, now + seconds))
        return replace(self, sampled=now, deadline=deadline, sequence=seq,
                       last_seconds=seconds)

    def receipt(self):
        return dict(schema=1, purpose='offline-backup', nonce=self.nonce,
                    boot_id=self.boot_id, owner=self.owner, sequence=self.sequence,
                    deadline_monotonic=self.deadline, hard_deadline_monotonic=self.started + 86400,
                    root_write_authorized=False, normal_boot_release_authorized=False)
