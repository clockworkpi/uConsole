"""Pinned, chunk-verified host source for restoring a retained card's root.

No target access or writer is provided. Consumers must retain recovery hold on
any exception, and must independently verify the completed target root before
release. A successful source stream proves sent bytes, not restored bytes.
"""
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from forge_recovery_archive import fingerprint
from forge_target_journal import private_directory, read_record, write_record

CHUNK_BYTES = 4 * 1024 * 1024
MAX_CHUNKS = 131072


def digest(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n').encode()).hexdigest()


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def root_checked(value, card_bytes):
    if (not isinstance(value, dict) or set(value) != {'offset','bytes','sha256'} or
            any(type(value[key]) is not int or value[key] <= 0 or value[key] % 512
                for key in ('offset','bytes')) or not sha(value['sha256']) or
            value['offset'] + value['bytes'] > card_bytes or
            value['bytes'] > CHUNK_BYTES * MAX_CHUNKS):
        raise ValueError('Invalid aligned, bounded restore source root')


def validate(manifest, pin):
    if not sha(pin) or not isinstance(manifest, dict) or digest(manifest) != pin:
        raise ValueError('Restore source manifest differs from pinned digest')
    fields = {'schema','kind','backup_plan_sha256','backup_acceptance_sha256',
              'archive_fingerprint','card','root','chunk_bytes','chunks',
              'target_write_authorized','normal_boot_release_authorized'}
    if (set(manifest) != fields or type(manifest['schema']) is not int or manifest['schema'] != 1 or
            manifest['kind'] != 'backup-root-chunk-source' or
            manifest['target_write_authorized'] is not False or manifest['normal_boot_release_authorized'] is not False or
            any(not sha(manifest[key]) for key in ('backup_plan_sha256','backup_acceptance_sha256')) or
            type(manifest['chunk_bytes']) is not int or manifest['chunk_bytes'] != CHUNK_BYTES):
        raise ValueError('Unsupported restore source manifest')
    card = manifest['card']
    if (not isinstance(card, dict) or set(card) != {'bytes','sha256'} or
            type(card['bytes']) is not int or card['bytes'] <= 0 or card['bytes'] % 512 or not sha(card['sha256'])):
        raise ValueError('Invalid restore source card digest')
    root_checked(manifest['root'], card['bytes'])
    signature = manifest['archive_fingerprint']
    if not isinstance(signature, list) or len(signature) != 8 or any(type(value) is not int or value < 0 for value in signature):
        raise ValueError('Invalid archive identity fingerprint')
    chunks = manifest['chunks']
    length = manifest['root']['bytes']
    if not isinstance(chunks, list) or len(chunks) != (length + CHUNK_BYTES - 1)//CHUNK_BYTES:
        raise ValueError('Missing or excessive restore chunks')
    for index, chunk in enumerate(chunks):
        offset = index * CHUNK_BYTES
        if (not isinstance(chunk, dict) or set(chunk) != {'offset','bytes','sha256'} or
                type(chunk['offset']) is not int or chunk['offset'] != offset or
                type(chunk['bytes']) is not int or chunk['bytes'] != min(CHUNK_BYTES, length-offset) or
                not sha(chunk['sha256'])):
            raise ValueError('Noncontiguous or invalid restore chunk')
    return manifest


@contextmanager
def opened(directory, expected_root):
    fd = private_directory(directory)
    archive = None
    try:
        plan, accepted = read_record(fd, 'plan.json'), read_record(fd, 'acceptance.json')
        card = accepted.get('card', {})
        length = card.get('bytes')
        compressed = accepted.get('compressed_bytes')
        extent = plan.get('extent', {})
        if (plan.get('backup_kind') != 'whole-card-bytes' or accepted.get('status') != 'verified-card-byte-backup' or
                set(card) != {'bytes','sha256'} or type(length) is not int or length <= 0 or length % 512 or
                not sha(card['sha256']) or type(compressed) is not int or compressed <= 0 or
                not isinstance(plan.get('device'), str) or not re.fullmatch('/dev/mmcblk[0-9]{1,2}',plan['device']) or
                plan.get('source') != dict(device=plan['device'],length_bytes=length)):
            raise ValueError('Expected verified whole-card source backup')
        root_checked(expected_root, length)
        if (type(extent.get('disk_bytes')) is not int or extent['disk_bytes'] != length or
                type(extent.get('offset_bytes')) is not int or extent['offset_bytes'] != expected_root['offset'] or
                type(extent.get('length_bytes')) is not int or extent['length_bytes'] != expected_root['bytes']):
            raise ValueError('Approved root does not match backup geometry')
        archive = os.open('card.img.gz',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        initial = os.fstat(archive)
        if (not stat.S_ISREG(initial.st_mode) or initial.st_uid != os.getuid() or initial.st_mode & 0o077 or
                initial.st_nlink != 1 or initial.st_size != compressed):
            raise PermissionError('Restore archive must be private, owned, single-link and match receipt size')
        binding = dict(backup_plan_sha256=digest(plan),backup_acceptance_sha256=digest(accepted),
                       archive_fingerprint=list(fingerprint(initial)),card=dict(card))
        yield archive, binding
        if (fingerprint(initial) != fingerprint(os.fstat(archive)) or
                fingerprint(initial) != fingerprint(os.stat('card.img.gz',dir_fd=fd,follow_symlinks=False)) or
                digest(read_record(fd,'plan.json')) != digest(plan) or
                digest(read_record(fd,'acceptance.json')) != digest(accepted)):
            raise ValueError('Restore source changed during streaming')
    finally:
        if archive is not None: os.close(archive)
        os.close(fd)


def scan(archive, card, root, consume, heartbeat):
    """Visit only root chunks, but verify prefix/suffix and gzip completion too."""
    whole, hashed = hashlib.sha256(), hashlib.sha256()
    with os.fdopen(os.dup(archive),'rb') as raw, gzip.GzipFile(fileobj=raw,mode='rb') as source:
        def read(count):
            if heartbeat is not None: heartbeat()
            value = source.read(count)
            if len(value) != count: raise ValueError('Restore archive ended early')
            whole.update(value)
            return value
        remaining = root['offset']
        while remaining:
            count = min(1048576,remaining)
            read(count)
            remaining -= count
        offset = 0
        while offset < root['bytes']:
            data = read(min(CHUNK_BYTES,root['bytes']-offset))
            hashed.update(data)
            chunk = dict(offset=offset,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
            consume(chunk,data)
            offset += len(data)
        remaining = card['bytes']-root['offset']-root['bytes']
        while remaining:
            count = min(1048576,remaining)
            read(count)
            remaining -= count
        if source.read(1): raise ValueError('Restore archive expands beyond verified card length')
    if whole.hexdigest() != card['sha256'] or hashed.hexdigest() != root['sha256']:
        raise ValueError('Restore archive card or approved root checksum differs')


def prepare(backup_directory, destination, expected_root, *, heartbeat=None):
    """Read all backup bytes before publishing an immutable root-chunk manifest."""
    if heartbeat is not None and not callable(heartbeat): raise ValueError('Expected heartbeat callable')
    destination = Path(destination).absolute()
    expected_root = dict(expected_root)
    fd = None
    result = dict(status='incomplete',target_write_authorized=False,normal_boot_release_authorized=False)
    try:
        with opened(backup_directory, expected_root) as (archive, binding):
            destination.mkdir(mode=0o700)
            parent = os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(parent)
            finally: os.close(parent)
            fd = private_directory(destination)
            write_record(fd,'request.json',dict(source=str(Path(backup_directory).absolute()),
                                               expected_root=expected_root,**binding))
            chunks = []
            scan(archive,binding['card'],expected_root,lambda chunk,data: chunks.append(chunk),heartbeat)
            # Source stability is checked again by opened() on successful exit;
            # do not publish a completed manifest until that check passes.
        manifest = dict(schema=1,kind='backup-root-chunk-source',**binding,root=dict(expected_root),
                        chunk_bytes=CHUNK_BYTES,chunks=chunks,target_write_authorized=False,
                        normal_boot_release_authorized=False)
        pin = digest(manifest)
        validate(manifest,pin)
        if write_record(fd,'manifest.json',manifest) != pin:
            raise ValueError('Written manifest digest differs')
        result.update(status='verified-root-chunk-source',manifest_sha256=pin,chunk_count=len(chunks))
        return result
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        if fd is not None:
            try: write_record(fd,'acceptance.json',result)
            finally: os.close(fd)


def stream(backup_directory, manifest, pin, consume, *, heartbeat=None):
    """Call consume(chunk, bytes) only after that chunk matches the pinned map.

    Full success is reported only after EOF, CRC, whole-card/root hashes and
    source stability checks. Consumed chunks are not a restore receipt.
    """
    validate(manifest,pin)
    manifest = json.loads(json.dumps(manifest))
    if not callable(consume) or heartbeat is not None and not callable(heartbeat):
        raise ValueError('Expected synchronous chunk consumer and optional heartbeat')
    count = 0
    with opened(backup_directory,manifest['root']) as (archive,binding):
        if any(binding[key] != manifest[key] for key in binding):
            raise ValueError('Restore archive identity differs from frozen manifest')
        def checked(chunk,data):
            nonlocal count
            if chunk != manifest['chunks'][count]:
                raise ValueError('Restore chunk differs before transmission')
            consume(dict(chunk),data)
            count += 1
        scan(archive,binding['card'],manifest['root'],checked,heartbeat)
    return dict(status='verified-source-stream',manifest_sha256=pin,chunks=count,
                root=dict(manifest['root']),target_restore_verified=False,normal_boot_release_authorized=False)
