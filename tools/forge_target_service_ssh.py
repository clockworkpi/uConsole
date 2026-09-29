"""Owner-only service-envelope transport; not a general remote command API.

Each attempt retains its request before SSH. Reconciliation uses the same
envelope and target ledger, with a new host attempt directory. This does not
provision recovery, authorize dependencies, or replace pre-deployment backups.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import uuid

from forge_target_journal import private_directory, write_record
from forge_target_phases import canonical
from forge_target_service_runtime import validate
from forge_target_ssh import TargetUncertain


MODULES = ('forge_target_backup', 'forge_target_files', 'forge_target_links',
           'forge_target_services', 'forge_target_journal', 'forge_target_phases',
           'forge_target_effects', 'forge_target_ssh', 'forge_target_service_runtime')
BOOTSTRAP = '''import json, sys, types
request = json.load(sys.stdin)
for name, source in request['modules']:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, '<forge-owner:' + name + '>', 'exec'), module.__dict__)
from forge_target_service_runtime import execute
result = execute(request['envelope'], request['sha256'], ledger_parent=request['ledger_parent'])
print(json.dumps({'nonce': request['nonce'], 'result': result}))
'''


def dispatch(host, envelope, approved_sha256, directory, *, ledger_parent, timeout=300):
    """Dispatch an owner-approved envelope with private, exclusive evidence."""
    envelope, _ = validate(envelope)
    digest = hashlib.sha256(canonical(envelope).encode()).hexdigest()
    if digest != approved_sha256:
        raise PermissionError('Service envelope differs from owner-approved digest')
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH target')
    if (not isinstance(ledger_parent, str) or not ledger_parent.startswith('/') or
            str(Path(ledger_parent)) != ledger_parent or
            any(part in ('.', '..') for part in ledger_parent.split('/')) or ledger_parent == '/'):
        raise ValueError('Use a canonical owner-provisioned ledger directory')
    if type(timeout) is not int or not 1 <= timeout <= 1800:
        raise ValueError('SSH timeout must be 1..1800 seconds')
    root = Path(__file__).resolve().parent
    request = {'modules': [(name, (root / (name + '.py')).read_text()) for name in MODULES],
               'envelope': envelope, 'sha256': digest, 'ledger_parent': ledger_parent,
               'nonce': uuid.uuid4().hex}
    payload = json.dumps(request)
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    fd = private_directory(directory)
    try:
        write_record(fd, 'dispatch.json', dict(request, host=host))
        parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        try:
            process = subprocess.run(
                ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                 'sudo -n python3 -c ' + shlex.quote(BOOTSTRAP)],
                input=payload, text=True, capture_output=True, timeout=timeout)
            if process.returncode:
                raise TargetUncertain('Remote service executor failed: ' + process.stderr[-2000:])
            response = json.loads(process.stdout)
            expected = {'status': 'verified', 'phases': len(envelope['plan']['phases']),
                        'ledger': str(Path(ledger_parent) / digest), 'envelope_sha256': digest}
            if canonical(response) != canonical({'nonce': request['nonce'], 'result': expected}):
                raise TargetUncertain('Invalid service acknowledgement')
            write_record(fd, 'acknowledged.json', response)
            return response['result']
        except BaseException as exc:
            write_record(fd, 'uncertain.json', {'nonce': request['nonce'], 'error': str(exc)})
            raise TargetUncertain('Service completion uncertain; retain both ledgers and reconcile') from exc
    finally:
        os.close(fd)
