"""Disposable-VM proof of durable lease ownership; no disk operations."""
import os
from pathlib import Path

from forge_recovery_session import Session, prepare
from forge_target_journal import private_directory, write_record


def run(probe, directory, boot_id, owner):
    if probe.mode != 'emulated':
        raise ValueError('Lease fault probe is limited to a disposable emulator')
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    result = dict(status='incomplete', physical_qualified=False, target_disk_written=False,
                  root_write_authorized=False, normal_boot_release_authorized=False)
    try:
        prepared = prepare(directory/'session', probe, boot_id, owner)
        write_record(fd, 'prepared.json', prepared)
        pin = prepared['binding_sha256']
        def open_session(connection):
            return Session(directory/'session', pin, connection, boot_id, owner)
        with open_session(probe) as lease:
            first = lease.renew()
            try:
                with open_session(probe):
                    raise AssertionError('Competing session acquired the owner lock')
            except BlockingIOError:
                pass
        lost = []
        class LoseReply:
            def __getattr__(_, name):
                return getattr(probe, name)
            def _observe(_, command):
                value = probe._observe(command)
                lost.append(value)
                write_record(fd, 'received-but-lost.json', value)
                raise ConnectionError('Injected loss after real target lease renewal')
        with open_session(LoseReply()) as lease:
            if lease.accepted != first: raise ValueError('Durable lease receipt changed on reopen')
            try:
                lease.renew()
            except ConnectionError as exc:
                if str(exc) != 'Injected loss after real target lease renewal': raise
            else:
                raise ValueError('Lease reply was not lost as intended')
            if len(lost) != 1 or not lease.unresolved:
                raise ValueError('Missing durable pending lease after reply loss')
        with open_session(probe) as lease:
            if not lease.unresolved or lease.accepted != first:
                raise ValueError('Pending lease was not retained across restart')
            try:
                lease.renew()
            except RuntimeError as exc:
                if 'explicit retry_pending()' not in str(exc): raise
            else:
                raise ValueError('Pending lease was automatically renewed')
            recovered = lease.retry_pending()
            if recovered != lost[0]['receipt'] or recovered['sequence'] != 2:
                raise ValueError('Explicit duplicate changed the original lease deadline')
            final = lease.renew()
            if final['sequence'] != 3 or final['hard_deadline_monotonic'] != first['hard_deadline_monotonic']:
                raise ValueError('Durable lease sequence or hard deadline differs')
        result.update(status='passed', receipt=final, competing_owner_refused=True,
                      lost_reply_retained=True, implicit_retry_refused=True,
                      exact_duplicate_without_extension=True, reopened_sequence_advanced=True)
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd, 'acceptance.json', result)
        os.close(fd)
    return result
