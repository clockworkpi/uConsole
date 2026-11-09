"""Internal restore data plane for an already authorized, exclusively held root.

This module never opens a path, acquires authority, mounts storage or releases
recovery hold. The enclosing executor MUST verify persistent hold, boot/layout
identity, protected ranges, host backup and explicit owner approval before
supplying a writable root-partition descriptor. There is no GUI/MCP entrypoint.
An exception invalidates this instance; never continue or automatically retry
an uncertain write. A new recovery attempt requires separate reconciliation.
Backup sources remain the default. A derivative requires the enclosing owner's
explicit expected_kind selection and still grants no device-opening authority.
"""
import hashlib
import json
import os

from forge_recovery_claim import range_digest
from forge_recovery_source_contract import BACKUP, validate


class RootChunkWriter:
    def __init__(self, root_fd, manifest, pin, *, expected_kind=BACKUP):
        validate(manifest, pin, expected_kind=expected_kind)
        if type(root_fd) is not int or root_fd < 0:
            raise ValueError('Expected held root-partition descriptor')
        self.fd = root_fd
        self.manifest = json.loads(json.dumps(manifest))
        self.pin = pin
        self.source_kind = expected_kind
        self.index = 0
        self.failed = False
        self.finished = False
        self.written = 0
        self.matched = 0

    def active(self):
        if self.failed or self.finished:
            raise RuntimeError('Restore writer is terminal; retain evidence and reconcile')

    def apply(self, chunk, data):
        self.active()
        try:
            if self.index >= len(self.manifest['chunks']):
                raise ValueError('Unexpected extra restore chunk')
            expected = self.manifest['chunks'][self.index]
            # JSON equality remains type-strict; bool offsets cannot pass as 0.
            if (not isinstance(chunk,dict) or
                    json.dumps(chunk,sort_keys=True,allow_nan=False) != json.dumps(expected,sort_keys=True) or
                    type(data) is not bytes or len(data) != expected['bytes'] or
                    hashlib.sha256(data).hexdigest() != expected['sha256']):
                raise ValueError('Restore chunk differs from pinned source before write')
            offset,length = expected['offset'],expected['bytes']
            existing = range_digest(self.fd,offset,length)
            written = 0
            if existing != expected['sha256']:
                while written < length:
                    count = os.pwrite(self.fd,data[written:],offset+written)
                    if type(count) is not int or not 0 < count <= length-written:
                        raise OSError('Restore write made invalid or no progress')
                    written += count
            # Flush even a matching chunk: it may be a previous attempt's
            # unacknowledged cached write. A match is not a durable receipt.
            os.fsync(self.fd)
            if range_digest(self.fd,offset,length) != expected['sha256']:
                raise ValueError('Restore chunk readback differs after synchronization')
            self.index += 1
            self.written += written
            self.matched += int(written == 0)
            return dict(status='verified-root-chunk',manifest_sha256=self.pin,index=self.index-1,
                        chunk=dict(expected),bytes_written=written,synchronized=True,
                        normal_boot_release_authorized=False)
        except BaseException:
            self.failed = True
            raise

    def finish(self, *, progress=None):
        self.active()
        try:
            if progress is not None and not callable(progress):
                raise ValueError('Expected final-hash progress callback')
            if self.index != len(self.manifest['chunks']):
                raise ValueError('Cannot complete an incomplete root stream')
            root = self.manifest['root']
            os.fsync(self.fd)
            if range_digest(self.fd,0,root['bytes'],progress=progress) != root['sha256']:
                raise ValueError('Final restored root digest differs')
            self.finished = True
            return dict(status='verified-root-bytes',manifest_sha256=self.pin,root=dict(root),
                        chunks=self.index,bytes_written=self.written,matching_chunks=self.matched,
                        protected_ranges_verified=False,physical_restore_qualified=False,
                        normal_boot_release_authorized=False)
        except BaseException:
            self.failed = True
            raise
