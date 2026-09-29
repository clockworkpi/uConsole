"""One bounded duplex restore exchange over an already established channel.

The caller owns authenticated process setup/teardown, private durable journals,
source-health review, and explicit owner authorization. No retries, boot release,
or SSH dispatch are hidden here. Every callback is installed owner code.
"""
import copy

from forge_recovery_commit_protocol import exact
from forge_recovery_restore_host_protocol import Protocol
from forge_recovery_restore_protocol import Sender, header, write_header


def exchange(channel, plan, pin, manifest, attempt, produce, *, authorize, renew,
             record, half_close, wait_success):
    """Stream verified source chunks, servicing worker control between them.

    produce(consume) must return the completed archive stream receipt. record
    must durably append before returning; in particular control responses and
    chunk intents must reach the host journal before bytes reach the target.
    half_close closes only the host-to-worker side. wait_success must check the
    actual child/SSH exit status under the same overall deadline, or raise.
    channel supplies bounded read/write/readline and flush operations.
    """
    if any(not callable(hook) for hook in (produce, authorize, renew, record, half_close, wait_success)):
        raise ValueError('Expected installed owner exchange hooks')
    protocol = Protocol(plan, pin, manifest, attempt)
    sender = Sender(channel, protocol.manifest, protocol.wire['manifest_sha256'], protocol.wire,
                    expected_kind=protocol.source_kind)

    def durable(kind, value):
        record(kind, copy.deepcopy(value))

    def receive_one():
        message = header(channel)
        durable('worker-message', message)
        action = protocol.consume(message)
        if action == 'control':
            if message['kind'] == 'approve':
                expected = dict(restore=pin, boot_id=protocol.wire['boot_id'], phase=message['phase'])
                approved = authorize(copy.deepcopy(protocol.plan), pin, message['phase'])
                if not exact(approved, expected):
                    raise ValueError('Owner refused or changed restore approval')
                durable('owner-approval', approved)
            lease = renew()
            reply = protocol.respond(lease)
            durable('control-response-intent', reply)
            write_header(channel, reply)
            channel.flush()
        elif action == 'close-input':
            durable('input-close-intent', protocol.wire)
            half_close()
            protocol.input_closed()
        return action

    try:
        durable('exchange-intent', dict(protocol.wire))
        while protocol.phase != 'streaming':
            receive_one()

        def send_chunk(chunk, data):
            index = sender.index
            # Sender verifies source bytes before it writes. Any failure after
            # intent remains uncertain; the same attempt is never retried here.
            protocol.chunk_sent(index)
            durable('chunk-intent', dict(index=index, chunk=chunk, **protocol.wire))
            sender.chunk(chunk, data)
            while protocol.pending_chunk is not None:
                receive_one()

        receipt = produce(send_chunk)
        durable('source-verified', receipt)
        protocol.source_finished(receipt)
        sender.finish(receipt)
        while protocol.phase != 'complete':
            receive_one()
        if channel.read(1) != b'':
            raise ValueError('Unexpected bytes after restore worker completion')
        wait_success()
        durable('exchange-complete', protocol.result)
        return copy.deepcopy(protocol.result)
    except BaseException:
        protocol.failed = True
        # Journaling the failure itself belongs to the outer attempt owner;
        # failure of its disk must not cause another write attempt or hide the
        # original error behind an unsuccessful diagnostic append.
        raise
