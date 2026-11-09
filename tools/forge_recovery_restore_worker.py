"""Installed-owner restore worker hooks; not a remotely exposed command yet.

Host transport must send one chunk and await its acknowledgement, servicing
control challenges meanwhile. Lease renewals are serialized through these
challenges, never a competing background client. Input remains open for final
readback control traffic until the worker explicitly requests its half-close.
"""
import copy
import math
import time

from forge_recovery_commit_liveness import budget
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_executor import execute
from forge_recovery_restore_protocol import checked_binding, header, write_header


class Control:
    def __init__(self, channel, binding, wire_binding):
        self.channel = channel
        self.binding = copy.deepcopy(binding)
        self.wire = checked_binding(wire_binding, wire_binding['manifest_sha256'])
        if self.wire['boot_id'] != self.binding['boot_id']:
            raise ValueError('Restore control boot differs')
        self.sequence = 0
        self.accepted = None
        self.last_renewal = None
        self.mounted = False
        self.finishing = False
        self.failed = False
        self.last_progress = {}

    def emit(self, message_type, **values):
        if self.failed or self.finishing:
            raise RuntimeError('Restore control is terminal')
        try:
            write_header(self.channel, dict(type=message_type, **self.wire, **values))
            self.channel.flush()
        except BaseException:
            self.failed = True
            raise

    def exchange(self, kind, phase):
        if self.failed or self.finishing or self.mounted:
            raise RuntimeError('Restore control exchange forbidden in this phase')
        self.sequence += 1
        challenge = dict(kind=kind, phase=phase, sequence=self.sequence)
        try:
            self.emit('control-required', **challenge)
            reply = header(self.channel)
            expected = dict(type='control-accepted', **self.wire, **challenge)
            if kind == 'approve':
                expected['restore'] = self.wire['plan_sha256']
            if (not isinstance(reply, dict) or 'lease' not in reply or
                    not exact({key: value for key, value in reply.items() if key != 'lease'}, expected)):
                raise ValueError('Restore control acknowledgement differs')
            accepted = copy.deepcopy(reply['lease'])
            if (self.accepted is not None and
                    (type(accepted.get('sequence')) is not int or
                     accepted['sequence'] <= self.accepted['sequence'])):
                raise ValueError('Restore control requires a fresh acknowledged renewal')
            # Checks the actual private local lease and live watchdog owner,
            # not a lease value or clock asserted by the remote reply alone.
            remaining = budget(self.binding, self.binding['lease_owner'], accepted)
            if (type(remaining) not in (int, float) or not math.isfinite(remaining) or
                    not 180 <= remaining <= 300):
                raise ValueError('Insufficient fresh restore control budget')
            self.accepted = accepted
            self.last_renewal = time.monotonic()
            return remaining
        except BaseException:
            self.failed = True
            raise

    def approve(self, phase, pin, binding):
        if (phase not in ('acquire', 'inspect', 'write') or
                pin != self.wire['plan_sha256'] or not exact(binding, self.binding)):
            raise ValueError('Restore approval binding differs')
        self.exchange('approve', phase)
        if phase == 'inspect':
            self.mounted = True  # Renewal stays suspended until verified unmount.
        return dict(restore=pin, boot_id=self.binding['boot_id'], phase=phase)

    def live_budget(self):
        if self.failed or self.finishing or self.accepted is None:
            raise RuntimeError('No live restore control acknowledgement')
        remaining = budget(self.binding, self.binding['lease_owner'], self.accepted)
        if remaining < 90 and not self.mounted:
            return self.exchange('renew', 'verification')
        return remaining

    def progress(self, name, done, total):
        if self.mounted or self.finishing or self.failed:
            raise RuntimeError('Restore progress in forbidden control phase')
        if (self.last_renewal is None or time.monotonic()-self.last_renewal >= 60):
            self.exchange('renew', 'verification')
        previous = self.last_progress.get(name)
        if previous is None or done < previous or done-previous >= 64*1024*1024 or done == total:
            self.emit('progress', range=name, bytes=done, total=total)
            self.last_progress[name] = done

    def unmounted(self):
        if not self.mounted:
            raise RuntimeError('Unexpected restore unmount')
        self.mounted = False
        self.emit('unmounted')

    def acknowledge(self, message):
        if self.mounted or self.finishing or self.failed:
            raise RuntimeError('Restore acknowledgement in forbidden phase')
        write_header(self.channel, message)
        self.channel.flush()

    def finish_input(self):
        if self.mounted:
            raise RuntimeError('Cannot close restore input while boot is mounted')
        self.emit('close-input')
        self.finishing = True


def run(request, channel):
    """Run with a validated owner-loaded request and a deadline-bounded PipeIO.

    A bootstrap/dispatch route is intentionally not provided here. The host
    must retain approved source-health evidence and every uncertain attempt.
    """
    if (not isinstance(request, dict) or
            set(request) != {'protocol', 'plan', 'pin', 'inputs', 'attempt'} or
            type(request['protocol']) is not int or request['protocol'] != 1):
        raise ValueError('Unexpected restore worker request')
    request = copy.deepcopy(request)
    plan, pin, attempt = request['plan'], request['pin'], request['attempt']
    wire = dict(plan_sha256=pin, manifest_sha256=plan['source_manifest_sha256'],
                attempt=attempt, boot_id=plan['binding']['boot_id'])
    control = Control(channel, plan['binding'], wire)
    result = execute(plan, pin, request['inputs'], attempt, channel,
                     approve=control.approve, live_budget=control.live_budget,
                     acknowledge=control.acknowledge, progress=control.progress,
                     unmounted=control.unmounted, finish_input=control.finish_input)
    # Historical ledger replay can return without an effect or close-input
    # exchange. It is explicitly distinguished from a fresh verified effect.
    write_header(channel, dict(type='complete', **wire, result=result,
                               historical_replay=not control.finishing))
    channel.flush()
    return result
