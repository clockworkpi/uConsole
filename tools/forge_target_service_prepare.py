"""Read-only SSH preparation of standalone service pairs for explicit review.

No ledger provisioning, service commands, deployment or grant. Unit executable
effects and application-data consistency still require owner review.
"""
import json
import os
from pathlib import Path
import time

from forge_target_backup import capture as capture_files, verify as verify_files
from forge_target_services import capture as capture_services, verify as verify_services, service_names
from forge_target_prepare import freeze_sources
from forge_target_effects import fingerprint
from forge_target_service_compile import new_service, update_service
from forge_target_service_transaction import prepare
from forge_target_journal import private_directory, write_record


def author(output, host, unit, source, *, active=True):
    service_names([unit])
    if type(active) is not bool:
        raise ValueError('Desired activity must be explicit')
    path = '/etc/systemd/system/' + unit
    link_path = '/etc/systemd/system/multi-user.target.wants/' + unit
    frozen = freeze_sources([{'source': str(source), 'target': path}])[0]
    output = Path(output).absolute()
    output.mkdir(mode=0o700)
    fd = private_directory(output)
    try:
        parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        capture_services(host, [unit], output / 'services-before.json', with_dependencies=True)
        capture_files(host, [path], output / 'files-before.json')
        capture_files(host, [path], output / 'files-confirmed.json')
        capture_services(host, [unit], output / 'services-confirmed.json', with_dependencies=True)
        first = verify_services(json.loads((output / 'services-before.json').read_text()), [unit])
        last = verify_services(json.loads((output / 'services-confirmed.json').read_text()), [unit])
        before = verify_files(json.loads((output / 'files-before.json').read_text()), [path])
        confirmed = verify_files(json.loads((output / 'files-confirmed.json').read_text()), [path])
        if (first != last or before['machine_id'] != first['machine_id'] or
                confirmed['machine_id'] != first['machine_id'] or
                [fingerprint(item) for item in before['files']] != [fingerprint(item) for item in confirmed['files']]):
            raise ValueError('Target changed during preparation; retained captures are not an approved transaction')
        # The compiler handles exactly this one explicit enablement link, not
        # aliases, runtime links, additional targets or unit-file drop-ins.
        links = first['mutable_links']['links']
        if len(links) > 1 or (links and links[0]['path'] != link_path):
            raise ValueError('Additional enablement or alias links require broader recovery coverage')
        link = dict(links[0], kind='symlink') if links else {'path': link_path, 'kind': 'absent'}
        old = before['files'][0]
        stamp = time.time_ns()
        desired = dict(old) if old['kind'] == 'file' else {
            'path': path, 'kind': 'file', 'uid': 0, 'gid': 0, 'mode': 0o644, 'atime_ns': stamp, 'xattrs': {}}
        if desired['mode'] & 0o6000 or 'security.capability' in desired['xattrs']:
            raise ValueError('Privileged unit metadata requires separate review')
        desired.update(data=frozen['data'], size=frozen['size'], sha256=frozen['sha256'], mtime_ns=stamp)
        state = {key: value for key, value in first['services'][unit].items() if key != 'SubState'}
        backup = {'identity': {key: first[key] for key in ('machine_id', 'boot_id')},
                  'files': [old], 'links': [link],
                  'state': {'services': {unit: state}, 'files': {path: fingerprint(old)}, 'links': {link_path: link}}}
        if state['LoadState'] == 'not-found':
            if not active:
                raise ValueError('New-service compiler currently requires an active desired service')
            desired_link = {'path': link_path, 'kind': 'symlink', 'target': path,
                            'uid': 0, 'gid': 0, 'mtime_ns': stamp}
            pair = new_service(backup, unit, desired, desired_link)
        else:
            pair = update_service(backup, unit, desired, active=active)
        prepared = prepare(output / 'transaction', pair['backup'], pair['apply'], pair['restore'])
        review = dict(prepared, kind='service', host=host, unit=unit, journal=str(output / 'transaction'),
                      identity=backup['identity'], source=frozen['source'],
                      before={key: old[key] for key in ('kind', 'sha256', 'mode', 'size') if key in old},
                      after={key: desired[key] for key in ('sha256', 'mode', 'size')},
                      original_activity=state['ActiveState'], desired_activity='active' if active else 'inactive',
                      dependencies=first['dependencies'], deployment_performed=False, authorization_performed=False,
                      pending_review=['unit executable and dependency effects', 'application-data consistency',
                                      'owner-provisioned target ledger and explicit authorization'])
        write_record(fd, 'review.json', review)
        return review
    except BaseException as exc:
        write_record(fd, 'incomplete.json', {'error': str(exc), 'deployment_performed': False})
        raise
    finally:
        os.close(fd)
