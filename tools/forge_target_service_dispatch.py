"""Owner-bound dispatch of paired service transactions with direction recovery.

Not exposed to MCP: dependency and application-data review remain prerequisites.
Authorization binds the retained transaction, SSH host and private target ledger.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid

from forge_target_journal import private_directory, read_record, write_record, events, append_event
from forge_target_phases import canonical
from forge_target_service_transaction import validate
from forge_target_service_ssh import dispatch as dispatch_envelope
from forge_target_ssh import TargetUncertain


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@contextmanager
def locked(directory):
    fd = private_directory(directory)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def transaction(fd, approved_sha256):
    record = read_record(fd, 'transaction.json')
    if (not isinstance(record, dict) or set(record) != {'schema', 'kind', 'backup', 'apply', 'restore'} or
            record != validate(record['backup'], record['apply'], record['restore'])):
        raise ValueError('Invalid paired service transaction')
    if digest(record) != approved_sha256:
        raise PermissionError('Service transaction differs from reviewed digest')
    return record


def authorize(directory, approved_transaction_sha256, *, host, ledger_parent):
    """Persist an explicit owner decision; does not contact or modify target."""
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH host')
    if (not isinstance(ledger_parent, str) or not ledger_parent.startswith('/') or
            str(Path(ledger_parent)) != ledger_parent or ledger_parent == '/' or
            any(part in ('.', '..') for part in ledger_parent.split('/'))):
        raise ValueError('Use a canonical owner-provisioned target ledger directory')
    with locked(directory) as fd:
        transaction(fd, approved_transaction_sha256)
        binding = {'schema': 1, 'transaction_sha256': approved_transaction_sha256,
                   'host': host, 'ledger_parent': ledger_parent}
        write_record(fd, 'authorization.json', binding)  # Exclusive: never rebind a used transaction.
        return {'status': 'authorized', 'authorization_sha256': digest(binding)}


def provision_and_authorize(directory, approved_transaction_sha256, *, host):
    """Explicit owner action: create only a private target ledger, not a service."""
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH host')
    worker = '''import json,sys,tempfile,os
from pathlib import Path
r=json.load(sys.stdin)
identity={'machine_id':Path('/etc/machine-id').read_text().strip(),
          'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
if identity != r['identity']: raise ValueError('Target identity differs from reviewed backup')
directory=tempfile.mkdtemp(prefix='uconsole-forge-service-',dir='/var/tmp')
fd=os.open('/var/tmp',os.O_RDONLY|os.O_DIRECTORY)
try: os.fsync(fd)
finally: os.close(fd)
print(json.dumps({'nonce':r['nonce'],'identity':identity,'ledger_parent':directory}))
'''
    with locked(directory) as fd:
        record = transaction(fd, approved_transaction_sha256)
        if 'authorization.json' in os.listdir(fd):
            raise ValueError('Transaction is already authorized; do not provision another ledger')
        request = {'nonce': uuid.uuid4().hex, 'identity': record['backup']['identity']}
        # Exclusive intent prevents blind retries after a lost SSH result.
        write_record(fd, 'provision-intent.json', dict(request, host=host, source=worker))
        try:
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                                     'sudo -n python3 -c ' + shlex.quote(worker)], input=json.dumps(request),
                                    text=True, capture_output=True, timeout=30)
            if result.returncode:
                raise TargetUncertain('Target ledger provisioning failed: ' + result.stderr[-1000:])
            response = json.loads(result.stdout)
            if (set(response) != {'nonce', 'identity', 'ledger_parent'} or
                    response['nonce'] != request['nonce'] or response['identity'] != request['identity'] or
                    not re.fullmatch('/var/tmp/uconsole-forge-service-[A-Za-z0-9_-]+', response['ledger_parent'])):
                raise TargetUncertain('Invalid target ledger acknowledgement')
            write_record(fd, 'provisioned.json', response)
            binding = {'schema': 1, 'transaction_sha256': approved_transaction_sha256,
                       'host': host, 'ledger_parent': response['ledger_parent']}
            write_record(fd, 'authorization.json', binding)
            return {'status': 'authorized', 'authorization_sha256': digest(binding),
                    'journal': str(Path(directory).absolute()), 'kind': 'service',
                    'deployment_performed': False}
        except BaseException as exc:
            write_record(fd, 'provision-uncertain.json', {'error': str(exc), 'deployment_performed': False})
            raise TargetUncertain('Ledger provisioning uncertain; retain records and inspect before retrying') from exc


def dispatch(directory, direction, approved_authorization_sha256, *, timeout=300):
    if direction not in ('apply', 'restore'):
        raise ValueError('Choose apply or restore')
    directory = Path(directory).absolute()
    with locked(directory) as fd:
        binding = read_record(fd, 'authorization.json')
        if (not isinstance(binding, dict) or
                set(binding) != {'schema', 'transaction_sha256', 'host', 'ledger_parent'} or
                type(binding['schema']) is not int or binding['schema'] != 1 or
                digest(binding) != approved_authorization_sha256):
            raise PermissionError('Service authorization differs from owner-approved digest')
        record = transaction(fd, binding['transaction_sha256'])
        history = events(fd)
        prior = None
        for event in history:
            if (event.get('state') not in ('dispatch', 'acknowledged', 'uncertain') or
                    event.get('direction') not in ('apply', 'restore') or
                    event.get('authorization_sha256') != approved_authorization_sha256 or
                    not isinstance(event.get('nonce'), str) or not re.fullmatch('[0-9a-f]{32}', event['nonce']) or
                    event.get('attempt') != 'attempt-' + event['nonce']):
                raise TargetUncertain('Invalid service history; retain evidence and reconcile')
            if event['state'] == 'dispatch':
                if ((prior is None and event['direction'] != 'apply') or
                        (prior is not None and prior['direction'] == 'restore' and event['direction'] == 'apply') or
                        (prior is not None and prior['state'] != 'acknowledged' and
                         prior['direction'] != event['direction'])):
                    raise TargetUncertain('Service history reverses an unresolved or restored transaction')
            elif (prior is None or prior['state'] != 'dispatch' or
                  any(prior[key] != event[key] for key in ('nonce', 'attempt', 'direction'))):
                raise TargetUncertain('Service history result has no matching dispatch')
            prior = event
        if not history and direction != 'apply':
            raise ValueError('Restore requires an acknowledged apply')
        if history:
            previous = history[-1]
            if previous['state'] != 'acknowledged' and previous['direction'] != direction:
                raise TargetUncertain('Reconcile the uncertain direction before reversing')
            if previous['direction'] == 'restore' and direction == 'apply':
                raise ValueError('Prepare a new transaction for another development cycle')
        nonce = uuid.uuid4().hex
        event = {'state': 'dispatch', 'direction': direction, 'nonce': nonce,
                 'authorization_sha256': approved_authorization_sha256, 'attempt': 'attempt-' + nonce}
        append_event(fd, event)
        envelope = record[direction]
        try:
            result = dispatch_envelope(binding['host'], envelope, digest(envelope), directory / event['attempt'],
                                       ledger_parent=binding['ledger_parent'], timeout=timeout)
            append_event(fd, dict(event, state='acknowledged', result=result))
            return dict(result, direction=direction, authorization_sha256=approved_authorization_sha256)
        except BaseException as exc:
            append_event(fd, dict(event, state='uncertain', error=str(exc)))
            raise TargetUncertain('Paired service transition uncertain; reconcile before reversing') from exc
