"""Crash real image publication workers against private disposable storage.

No boot paths, policies, or block devices are changed. Retain partial images and
ledgers for inspection. Process exit models lost transport without relying on
exception cleanup, at three explicitly injected boundaries.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import uuid

from forge_recovery_image import publish, remove
from forge_recovery_ledger import run, fence
from forge_target_journal import private_directory, write_record


def worker(directory, image_directory, request, digest, size, token, boundary):
    directory = Path(directory)
    source, destination = directory / 'source.img', Path(image_directory) / 'destination.img'
    if boundary == 'partial-copy':
        original_write = os.write
        def interrupted_write(fd, data):
            count = original_write(fd, data)
            if os.readlink('/proc/self/fd/' + str(fd)).endswith('.forge-image-' + token):
                os.fsync(fd)
                os._exit(73)
            return count
        os.write = interrupted_write
    def effect():
        result = publish(str(source), str(destination), digest, size, token)
        if boundary == 'published-before-receipt':
            os._exit(73)
        return result
    run(directory / 'ledger', request['nonce'], request, effect)
    os._exit(74)  # Durable receipt exists, but no response reaches the caller.


def qualify(directory, image_parent=None):
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    if image_parent is not None:
        image_parent = Path(image_parent)
        image_parent.mkdir(mode=0o700)
    observations = []
    context = multiprocessing.get_context('spawn')
    payload = b'forge-disposable-interruption-fixture\n' * 100000
    digest = hashlib.sha256(payload).hexdigest()
    for boundary in ('partial-copy', 'published-before-receipt', 'receipt-before-ack'):
        case = directory / boundary
        case.mkdir(mode=0o700)
        image_directory = case if image_parent is None else image_parent / boundary
        if image_directory != case:
            image_directory.mkdir(mode=0o700)
        ledger = case / 'ledger'
        ledger.mkdir(mode=0o700)
        with os.fdopen(os.open(case / 'source.img', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        token, nonce = uuid.uuid4().hex, uuid.uuid4().hex
        request = dict(plan_sha256=hashlib.sha256(boundary.encode()).hexdigest(), direction='apply', nonce=nonce)
        process = context.Process(target=worker, args=(str(case), str(image_directory), request, digest, len(payload), token, boundary))
        process.start()
        try:
            process.join(30)
            if process.is_alive():
                raise RuntimeError('Interruption worker did not reach the injected boundary')
            expected_exit = 74 if boundary == 'receipt-before-ack' else 73
            if process.exitcode != expected_exit:
                raise RuntimeError('Unexpected interruption worker exit: ' + str(process.exitcode))
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
        result = fence(ledger, nonce, request)
        destination = image_directory / 'destination.img'
        scratch = image_directory / ('.forge-image-' + token)
        def forbidden():
            raise AssertionError('An interrupted operation was executed again')
        if boundary == 'receipt-before-ack':
            expected = dict(status='published', sha256=digest, size=len(payload))
            if result != dict(status='completed', request=request, result=expected):
                raise RuntimeError('Lost acknowledgement did not reconcile to exact durable receipt')
            if run(ledger, nonce, request, forbidden) != expected:
                raise RuntimeError('Receipt replay differs')
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Published image differs')
            restore_nonce = uuid.uuid4().hex
            run(ledger, restore_nonce, dict(request, nonce=restore_nonce, direction='restore'),
                lambda: remove(str(destination), digest, len(payload), token))
            if destination.exists():
                raise RuntimeError('Acknowledged image was not restored to absence')
        else:
            if result != dict(status='incomplete', request=request):
                raise RuntimeError('Partial effect was incorrectly resolved')
            for attempt in (nonce, uuid.uuid4().hex):
                try:
                    run(ledger, attempt, dict(request, nonce=attempt), forbidden)
                except RuntimeError:
                    pass
                else:
                    raise RuntimeError('Incomplete effect allowed a retry')
            if boundary == 'partial-copy':
                if destination.exists() or not 0 < scratch.stat().st_size < len(payload):
                    raise RuntimeError('Partial-copy evidence differs')
            elif scratch.exists() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise RuntimeError('Publication-before-receipt evidence differs')
        observations.append(dict(boundary=boundary, exitcode=process.exitcode,
                                 reconciliation=result['status'], destination_present=destination.exists(),
                                 scratch_present=scratch.exists()))
    evidence = dict(status='passed', observations=observations, fixture=str(directory),
                    boot_paths_changed=False, scope='Real file publication with injected process exits; not an SSH disconnect')
    fd = private_directory(directory)
    try:
        write_record(fd, 'acceptance.json', evidence)
    finally:
        os.close(fd)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(qualify(args.output), indent=2))
