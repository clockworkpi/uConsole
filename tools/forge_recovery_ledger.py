"""Durable per-plan target attempt receipts; caller holds the target lock."""
import os
import re
from pathlib import Path

from forge_target_journal import private_directory, read_record, write_record


def validate_request(nonce, request):
    if (not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce)
            or not isinstance(request, dict) or set(request) != {'plan_sha256', 'direction', 'nonce'}
            or request['nonce'] != nonce or request['direction'] not in ('apply', 'restore')
            or not isinstance(request['plan_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', request['plan_sha256'])):
        raise ValueError('Invalid target attempt request')


def fence(directory, nonce, request):
    """Durably refuse this exact not-yet-started request, never undo an effect."""
    validate_request(nonce, request)
    fd = private_directory(directory)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent, receipt, marker = ('intent-' + nonce + '.json', 'receipt-' + nonce + '.json',
                                   'fence-' + nonce + '.json')
        names = os.listdir(fd)
        if marker in names:
            if intent in names or receipt in names or read_record(fd, marker) != request:
                raise ValueError('Conflicting target fence')
        elif intent in names:
            if read_record(fd, intent) != request:
                raise ValueError('Attempt identity differs')
            if receipt not in names:
                return {'status': 'incomplete', 'request': request}
            record = read_record(fd, receipt)
            if (not isinstance(record, dict) or set(record) != {'request', 'result'} or
                    record['request'] != request or not isinstance(record['result'], dict)):
                raise ValueError('Invalid target receipt')
            return {'status': 'completed', **record}
        elif receipt in names:
            raise RuntimeError('Orphaned target receipt')
        else:
            write_record(fd, marker, request)
        return {'status': 'fenced-not-started', 'request': request}
    finally:
        os.close(fd)


def run(directory, nonce, request, effect):
    """Execute once or return the durable receipt; never repeat partial work."""
    validate_request(nonce, request)
    fd = private_directory(directory)
    try:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        intent = 'intent-' + nonce + '.json'
        receipt = 'receipt-' + nonce + '.json'
        names = os.listdir(fd)
        marker = 'fence-' + nonce + '.json'
        if marker in names:
            if read_record(fd, marker) != request or intent in names or receipt in names:
                raise ValueError('Conflicting target fence')
            raise RuntimeError('Target request was fenced before starting')
        if intent in names:
            if read_record(fd, intent) != request:
                raise ValueError('Attempt nonce was reused for another request')
            if receipt in names:
                record = read_record(fd, receipt)
                if set(record) != {'request', 'result'} or record['request'] != request:
                    raise ValueError('Invalid target receipt')
                return record['result']
            raise RuntimeError('Target attempt is incomplete; reconciliation required')
        if receipt in names:
            raise RuntimeError('Orphaned target receipt')
        for name in names:
            if not re.fullmatch(r'(?:intent|receipt|fence)-[0-9a-f]{32}\.json', name):
                raise RuntimeError('Unexpected target ledger entry')
            if name.startswith('fence-'):
                prior_nonce = name[len('fence-'):-len('.json')]
                validate_request(prior_nonce, read_record(fd, name))
                if 'intent-' + prior_nonce + '.json' in names or 'receipt-' + prior_nonce + '.json' in names:
                    raise ValueError('Conflicting prior target fence')
            if name.startswith('receipt-') and 'intent-' + name[len('receipt-'):] not in names:
                raise RuntimeError('Orphaned target receipt')
            if name.startswith('intent-'):
                prior_receipt = 'receipt-' + name[len('intent-'):]
                if prior_receipt not in names:
                    raise RuntimeError('Another target attempt is incomplete')
                prior = read_record(fd, prior_receipt)
                if (not isinstance(prior, dict) or set(prior) != {'request', 'result'} or
                        prior['request'] != read_record(fd, name) or not isinstance(prior['result'], dict)):
                    raise ValueError('Invalid prior target receipt')
        write_record(fd, intent, request)
        result = effect()
        write_record(fd, receipt, {'request': request, 'result': result})
        return result
    finally:
        os.close(fd)


def provision(digest):
    """Create only root-owned private ledger directories, without symlink traversal."""
    if os.geteuid() != 0 or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Root and a valid plan digest are required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in ('var', 'lib'):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        for name in ('uconsole-forge-recovery', digest):
            try:
                os.mkdir(name, mode=0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != 0 or info.st_mode & 0o077:
                os.close(child)
                raise PermissionError('Unsafe target ledger directory')
            os.close(fd)
            fd = child
        os.fsync(fd)
    finally:
        os.close(fd)
    return Path('/var/lib/uconsole-forge-recovery') / digest
