"""Fail-closed host state machine for one new restore transport attempt.

This validates remote evidence; it grants no owner approval and performs no
I/O. The transport must journal explicit owner/source-health approval before
answering approval challenges and use its authenticated lease client to renew.
Historical receipts belong to reconciliation, never a fresh dispatch's success.
"""
from contextlib import contextmanager
import copy
import math

from forge_recovery_commit_protocol import exact
from forge_recovery_restore_ledger import request, checked_result
from forge_recovery_restore_protocol import checked_binding, completion
from forge_recovery_source_contract import validate
from forge_recovery_operation_contract import source_kind


class Protocol:
    def __init__(self, plan, pin, manifest, attempt):
        request(plan, pin, attempt)
        self.source_kind = source_kind(plan)
        validate(manifest, plan['source_manifest_sha256'], expected_kind=self.source_kind)
        if not exact(plan['root_after'], manifest['root']):
            raise ValueError('Restore host source root differs from pinned plan')
        self.plan, self.manifest = copy.deepcopy((plan, manifest))
        self.pin = pin
        self.wire = checked_binding(dict(plan_sha256=pin,
            manifest_sha256=plan['source_manifest_sha256'], attempt=attempt,
            boot_id=plan['binding']['boot_id']), plan['source_manifest_sha256'])
        self.phase = 'initial'
        self.sequence = 0
        self.lease_sequence = 0
        self.pending_control = None
        self.pending_chunk = None
        self.chunks = 0
        self.written = 0
        self.source_complete = False
        self.root_readback_complete = False
        self.failed = False
        self.result = None

    @contextmanager
    def operation(self):
        if self.failed or self.phase == 'complete':
            raise RuntimeError('Restore host protocol is terminal')
        try:
            yield
        except BaseException:
            self.failed = True
            raise

    def message(self, value, kind, fields):
        if (not isinstance(value, dict) or set(value) != {'type', *self.wire, *fields} or
                value['type'] != kind or
                not exact({key: value[key] for key in self.wire}, self.wire)):
            raise ValueError('Restore worker message binding or fields differ')

    def consume(self, value):
        with self.operation():
            if self.pending_control is not None:
                raise ValueError('Worker continued before owner control response')
            kind = value.get('type') if isinstance(value, dict) else None
            if kind == 'control-required':
                self.message(value, kind, ('kind', 'phase', 'sequence'))
                if type(value['sequence']) is not int or value['sequence'] != self.sequence+1:
                    raise ValueError('Restore control sequence differs')
                if value['kind'] == 'approve':
                    required = {'initial': 'acquire', 'acquired': 'inspect', 'unmounted': 'write'}
                    if value['phase'] != required.get(self.phase):
                        raise ValueError('Restore approval phase differs')
                elif value['kind'] == 'renew':
                    if value['phase'] != 'verification' or self.phase not in ('acquired', 'unmounted', 'streaming'):
                        raise ValueError('Restore renewal during forbidden phase')
                else:
                    raise ValueError('Unknown restore control challenge')
                self.sequence += 1
                self.pending_control = copy.deepcopy(value)
                return 'control'
            if kind == 'unmounted':
                self.message(value, kind, ())
                if self.phase != 'inspecting': raise ValueError('Unexpected restore unmount')
                self.phase = 'unmounted'
                return 'unmounted'
            if kind == 'progress':
                self.message(value, kind, ('range', 'bytes', 'total'))
                if self.phase not in ('acquired', 'unmounted', 'streaming'):
                    raise ValueError('Restore progress during forbidden phase')
                name = value['range']
                ranges = dict(root=self.plan['root_before'], prefix=self.plan['prefix_guard'],
                              suffix=self.plan['suffix_guard'], **{'restored-root': self.plan['root_after']})
                if (not isinstance(name, str) or name not in ranges or
                        type(value['bytes']) is not int or type(value['total']) is not int or
                        value['total'] != ranges[name]['bytes'] or not 0 <= value['bytes'] <= value['total']):
                    raise ValueError('Restore progress geometry differs')
                if name == 'restored-root':
                    if not self.source_complete: raise ValueError('Readback preceded source completion')
                    if value['bytes'] == value['total']: self.root_readback_complete = True
                return 'progress'
            if kind == 'chunk-verified':
                self.message(value, kind, ('index', 'result'))
                if self.phase != 'streaming' or self.pending_chunk is None:
                    raise ValueError('Unsolicited restore chunk acknowledgement')
                if type(value['index']) is not int or value['index'] != self.pending_chunk:
                    raise ValueError('Restore chunk acknowledgement order differs')
                chunk = self.manifest['chunks'][self.pending_chunk]
                result = value['result']
                written = result.get('bytes_written') if isinstance(result, dict) else None
                expected = dict(status='verified-root-chunk', manifest_sha256=self.wire['manifest_sha256'],
                    index=self.pending_chunk, chunk=chunk, bytes_written=written,
                    synchronized=True, normal_boot_release_authorized=False)
                if type(written) is not int or written not in (0, chunk['bytes']) or not exact(result, expected):
                    raise ValueError('Restore chunk verification differs from source')
                self.chunks += 1
                self.written += written
                self.pending_chunk = None
                return 'chunk'
            if kind == 'close-input':
                self.message(value, kind, ())
                if (self.phase != 'streaming' or not self.source_complete or
                        not self.root_readback_complete or self.pending_chunk is not None):
                    raise ValueError('Premature restore input-close request')
                self.phase = 'closing'
                return 'close-input'
            if kind == 'complete':
                self.message(value, kind, ('result', 'historical_replay'))
                if self.phase != 'input-closed' or value['historical_replay'] is not False:
                    raise ValueError('Premature or historical restore completion')
                result = checked_result(value['result'], self.plan, self.pin)
                if result['bytes_written'] != self.written:
                    raise ValueError('Restore completion byte count differs from chunk receipts')
                self.result = result
                self.phase = 'complete'
                return 'complete'
            raise ValueError('Unknown restore worker message')

    def respond(self, lease):
        """Called only after journaled owner approval and authenticated renewal."""
        with self.operation():
            if self.pending_control is None: raise ValueError('No restore control challenge')
            binding = self.plan['binding']
            expected = dict(schema=1, purpose='offline-backup', nonce=binding['nonce'],
                boot_id=binding['boot_id'], owner=binding['lease_owner'], root_write_authorized=False,
                normal_boot_release_authorized=False)
            if (not isinstance(lease, dict) or
                    set(lease) != {*expected, 'sequence', 'deadline_monotonic', 'hard_deadline_monotonic'} or
                    not exact({key: lease[key] for key in expected}, expected) or
                    type(lease['sequence']) is not int or not self.lease_sequence < lease['sequence'] <= 2**53-1 or
                    any(type(lease[key]) not in (int, float) or not math.isfinite(lease[key]) or lease[key] < 0
                        for key in ('deadline_monotonic', 'hard_deadline_monotonic')) or
                    lease['deadline_monotonic'] > lease['hard_deadline_monotonic']):
                raise ValueError('Restore host renewal receipt differs')
            control = self.pending_control
            reply = dict(control, type='control-accepted', lease=copy.deepcopy(lease))
            if control['kind'] == 'approve':
                reply['restore'] = self.pin
                self.phase = {'acquire': 'acquired', 'inspect': 'inspecting', 'write': 'streaming'}[control['phase']]
            self.lease_sequence = lease['sequence']
            self.pending_control = None
            return reply

    def chunk_sent(self, index):
        with self.operation():
            if (self.phase != 'streaming' or self.pending_control is not None or
                    self.pending_chunk is not None or self.source_complete or type(index) is not int or
                    index != self.chunks or index >= len(self.manifest['chunks'])):
                raise ValueError('Restore chunk sent out of order or without approval')
            self.pending_chunk = index

    def source_finished(self, receipt):
        with self.operation():
            if (self.phase != 'streaming' or self.pending_control is not None or self.pending_chunk is not None or
                    self.source_complete or self.chunks != len(self.manifest['chunks']) or
                    not exact(receipt, completion(self.manifest, self.wire['manifest_sha256'],
                                                 expected_kind=self.source_kind))):
                raise ValueError('Restore source completion differs or precedes acknowledged chunks')
            self.source_complete = True

    def input_closed(self):
        with self.operation():
            if self.phase != 'closing': raise ValueError('Restore input closed before verified readback')
            self.phase = 'input-closed'
