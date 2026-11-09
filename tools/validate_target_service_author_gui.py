"""Physical inert-service GUI author/apply/restore qualification; retains evidence."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import uuid

from forge_target_journal import private_directory, write_record
from target_gui_validation import GUIServiceAuthorTransitions
from validate_target_service_roundtrip import MODULES, PROBE, rpc

BODY = (b'[Unit]\nDescription=uConsole GUI author qualification\n'
        b'[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/usr/bin/true\n'
        b'[Install]\nWantedBy=multi-user.target\n')


def run(host, output):
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use a literal SSH hostname')
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    owner = None
    try:
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        unit = 'uconsole-forge-gui-' + uuid.uuid4().hex + '.service'
        path = '/etc/systemd/system/' + unit
        link = '/etc/systemd/system/multi-user.target.wants/' + unit
        scope = {'services': [unit], 'files': [path], 'links': [link]}
        root = Path(__file__).resolve().parent
        modules = [(name, (root / (name + '.py')).read_text()) for name in MODULES]

        def observe():
            nonce = uuid.uuid4().hex
            result = rpc(host, PROBE, {'modules': modules, 'scope': scope, 'nonce': nonce})
            if result.get('nonce') != nonce:
                raise ValueError('Invalid observation nonce')
            return result

        before = observe()
        state = before['state']
        if (state['services'][unit]['LoadState'] != 'not-found' or
                state['services'][unit]['ActiveState'] != 'inactive' or
                state['files'][path] != {'path': path, 'kind': 'absent'} or
                state['links'][link] != {'path': link, 'kind': 'absent'}):
            raise ValueError('Fixture must be absent before qualification')
        write_record(fd, 'before.json', before)
        source = output / 'fixture.service'
        with source.open('xb') as stream:
            stream.write(BODY)
            stream.flush()
            os.fsync(stream.fileno())
        owner = GUIServiceAuthorTransitions(output, host, source, unit, hashlib.sha256(BODY).hexdigest()).start()
        approved = observe()
        if approved['identity'] != before['identity'] or approved['state'] != before['state']:
            raise ValueError('Preparation or approval unexpectedly changed fixture state')
        owner.transition('apply')
        owner.transition('restore')
        after = observe()
        if after['identity'] != before['identity'] or after['state'] != before['state']:
            raise ValueError('GUI restore differs from original target state')
        finished, owner = owner, None
        finished.close()
        write_record(fd, 'acceptance.json', {'status': 'restored', 'unit': unit, 'after': after,
                     'scope': 'real GUI author/approve/apply/restore of inert service; programmatic dialogs'})
        return {'status': 'restored', 'unit': unit}
    except BaseException as exc:
        write_record(fd, 'incomplete.json', {'error': str(exc), 'restoration': 'unverified'})
        raise
    finally:
        try:
            if owner is not None:
                owner.close()
        finally:
            os.close(fd)


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    print(run(args.host, args.output))
