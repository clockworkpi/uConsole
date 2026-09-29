"""Passive systemd service-state preimages for live-target transactions.

No enable, start, stop, daemon-reload or file writes are performed on the target.
Unit/drop-in contents still require the file-backup step before deployment.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess

FIELDS = ('Id', 'LoadState', 'ActiveState', 'SubState', 'UnitFileState',
          'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Transient')
DEPENDENCIES = ('Requires', 'Requisite', 'Wants', 'BindsTo', 'PartOf', 'Upholds',
                'RequiredBy', 'WantedBy', 'BoundBy', 'UpheldBy', 'ConsistsOf',
                'Conflicts', 'Before', 'After', 'OnFailure', 'Triggers', 'TriggeredBy',
                'PropagatesStopTo', 'StopPropagatedFrom')


def parse_dependencies(output):
    """Literal unit names, including escaped paths and template instances.

    These are observations, not names authorized for lifecycle commands. Require
    every requested property: unsupported systemd properties are not empty edges.
    """
    result = {}
    for line in output.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in DEPENDENCIES or key in result:
            raise ValueError('Malformed or duplicate dependency property')
        units = dependency_words(value)
        if (len(units) > 4096 or len(set(units)) != len(units) or
                any(len(unit) > 255 or not re.fullmatch(
                    r'(?:[A-Za-z0-9_:.@-]|\\x[0-9a-fA-F]{2})+[.](?:service|socket|target|device|mount|automount|swap|timer|path|slice|scope)',
                    unit) for unit in units)):
            raise ValueError('Invalid dependency unit names')
        result[key] = sorted(units)
    if set(result) != set(DEPENDENCIES):
        raise ValueError('Incomplete dependency properties; unsupported is not empty')
    return result


def dependency_words(value):
    """Decode systemctl's quoted words without unescaping unit-name \\xNN literals."""
    units = []
    end = 0
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"|[^\s"]+', value):
        if value[end:match.start()].strip() or (end and match.start() == end):
            raise ValueError('Malformed dependency word separator')
        token = match.group()
        units.append(json.loads(token) if token.startswith('"') else token)
        end = match.end()
    if value[end:].strip():
        raise ValueError('Malformed dependency quoting')
    return units


def service_names(names):
    if (not isinstance(names, list) or not 1 <= len(names) <= 16 or
            any(not isinstance(name, str) or len(name) > 200 or
                not re.fullmatch('[A-Za-z0-9_][A-Za-z0-9_.-]*[.]service', name) for name in names) or
            len(set(names)) != len(names)):
        raise ValueError('Select 1..16 unique literal non-template .service names')
    return list(names)


def parse_state(name, output, *, allow_reload=False):
    values = {}
    for line in output.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in FIELDS or key in values:
            raise ValueError('Malformed or duplicate systemd property')
        values[key] = value
    if set(values) != set(FIELDS) or values['Id'] != name:
        raise ValueError('Incomplete or aliased service identity; use the canonical unit name')
    if values['NeedDaemonReload'] not in (('no', 'yes') if allow_reload else ('no',)) or values['Transient'] != 'no':
        raise ValueError('Transient or unreloaded unit cannot establish a stable preimage')
    if values['LoadState'] not in ('loaded', 'not-found', 'masked'):
        raise ValueError('Unsupported service load state')
    if values['ActiveState'] not in ('active', 'inactive', 'failed'):
        raise ValueError('Service is transitioning; capture again when stable')
    return values


def link_inventory(names, roots=('/etc/systemd/system', '/run/systemd/system')):
    """Capture mutable unit/enablement links without following linked directories.

    This is not the complete systemd search path or runtime dependency graph.
    Keep literal link targets; normalization is only for discovering references.
    """
    names = set(service_names(names))
    candidates = []
    availability = {}
    examined = 0

    def scan(directory, fd, descend):
        nonlocal examined
        with os.scandir(fd) as entries:
            for entry in entries:
                examined += 1
                if examined > 16384:
                    raise ValueError('Systemd link inventory exceeds entry limit')
                info = entry.stat(follow_symlinks=False)
                path = str(Path(directory) / entry.name)
                dependency_dir = entry.name.endswith(('.wants', '.requires', '.upholds'))
                if stat.S_ISLNK(info.st_mode):
                    if dependency_dir:
                        raise ValueError('Linked dependency directory cannot be safely inventoried: ' + path)
                    target = os.readlink(entry.name, dir_fd=fd)
                    after = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    if (info.st_ino, info.st_dev, info.st_ctime_ns) != (after.st_ino, after.st_dev, after.st_ctime_ns):
                        raise ValueError('Unit link changed during capture')
                    candidates.append({'path': path, 'target': target, 'uid': info.st_uid,
                                       'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns})
                elif descend and dependency_dir:
                    if not stat.S_ISDIR(info.st_mode):
                        raise ValueError('Dependency directory is not a real directory')
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        scan(path, child, False)
                    finally:
                        os.close(child)
                elif not descend and stat.S_ISDIR(info.st_mode):
                    raise ValueError('Unexpected nested dependency directory')

    for root in roots:
        root = str(root)
        try:
            info = os.lstat(root)
        except FileNotFoundError:
            availability[root] = False
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Unit search root must be a real directory')
        availability[root] = True
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            scan(root, fd, True)
        finally:
            os.close(fd)
    # Include aliases and their dependency links, including multi-hop aliases.
    selected = {}
    while True:
        previous = len(selected)
        for item in candidates:
            if Path(item['path']).name in names or Path(item['target']).name in names:
                selected[item['path']] = item
                names.add(Path(item['path']).name)
        if len(selected) == previous:
            break
    return {'scope': 'mutable system unit roots only; not a complete dependency graph',
            'roots': availability, 'links': [selected[path] for path in sorted(selected)]}


def capture_local(names, *, with_dependencies=False):
    names = service_names(names)
    machine_id = Path('/etc/machine-id').read_text().strip()
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def observe():
        states = {}
        dependencies = {}
        for name in names:
            fields = FIELDS + (DEPENDENCIES if with_dependencies else ())
            result = subprocess.run(['systemctl', 'show', '--all', '--property=' + ','.join(fields), '--', name],
                                    text=True, capture_output=True, timeout=10,
                                    env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'})
            if result.returncode:
                raise ValueError('Service inspection failed for ' + name + ': ' + result.stderr[-1000:])
            if len(result.stdout) > 65536:
                raise ValueError('Service properties exceed capture limit')
            lines = result.stdout.splitlines()
            if with_dependencies:
                dependencies[name] = parse_dependencies('\n'.join(
                    line for line in lines if line.partition('=')[0] in DEPENDENCIES))
                lines = [line for line in lines if line.partition('=')[0] not in DEPENDENCIES]
            states[name] = parse_state(name, '\n'.join(lines))
        return states, dependencies

    states, dependencies = observe()
    links = link_inventory(names)
    if (observe() != (states, dependencies) or link_inventory(names) != links or
            Path('/etc/machine-id').read_text().strip() != machine_id or
            Path('/proc/sys/kernel/random/boot_id').read_text().strip() != boot_id):
        raise ValueError('Service state or boot identity changed during capture')
    record = {'schema': 1, 'scope': 'systemd properties only; unit files and external service data not backed up',
            'machine_id': machine_id, 'boot_id': boot_id, 'services': states, 'mutable_links': links}
    if with_dependencies:
        record['dependencies'] = dependencies
    return record


def verify(record, names):
    names = service_names(names)
    if (type(record.get('schema')) is not int or record['schema'] != 1 or
            not re.fullmatch('[0-9a-f]{32}', record.get('machine_id', '')) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', record.get('boot_id', '')) or
            not isinstance(record.get('services'), dict) or set(record['services']) != set(names)):
        raise ValueError('Invalid service snapshot identity or service set')
    for name in names:
        values = record['services'][name]
        if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
            raise ValueError('Invalid service properties')
        if parse_state(name, '\n'.join(key + '=' + value for key, value in values.items())) != values:
            raise ValueError('Invalid service property values')
    if 'dependencies' in record:
        dependencies = record['dependencies']
        if not isinstance(dependencies, dict) or set(dependencies) != set(names):
            raise ValueError('Dependency observations must cover selected services exactly')
        for values in dependencies.values():
            if (not isinstance(values, dict) or set(values) != set(DEPENDENCIES) or
                    any(not isinstance(units, list) or any(not isinstance(unit, str) for unit in units)
                        for units in values.values())):
                raise ValueError('Invalid dependency observation')
            if parse_dependencies('\n'.join(key + '=' + ' '.join(units) for key, units in values.items())) != values:
                raise ValueError('Dependency observations must be canonical literal unit lists')
    if 'mutable_links' in record:
        graph = record['mutable_links']
        roots = ('/etc/systemd/system', '/run/systemd/system')
        if (not isinstance(graph, dict) or not isinstance(graph.get('roots'), dict) or
                set(graph['roots']) != set(roots) or any(type(v) is not bool for v in graph['roots'].values()) or
                not isinstance(graph.get('links'), list) or len(graph['links']) > 16384):
            raise ValueError('Invalid mutable unit link inventory')
        seen = set()
        for item in graph['links']:
            if (not isinstance(item, dict) or set(item) != {'path', 'target', 'uid', 'gid', 'mtime_ns'} or
                    not isinstance(item['path'], str) or not isinstance(item['target'], str) or
                    not item['target'] or '\0' in item['target']):
                raise ValueError('Invalid unit link record')
            path = Path(item['path'])
            if (str(path) != item['path'] or '..' in path.parts or item['path'] in seen or
                    not any(path.is_relative_to(root) and 1 <= len(path.relative_to(root).parts) <= 2 for root in roots) or
                    any(type(item[field]) is not int or item[field] < 0 for field in ('uid', 'gid', 'mtime_ns'))):
                raise ValueError('Invalid unit link path or metadata')
            root = next(root for root in roots if path.is_relative_to(root))
            parts = path.relative_to(root).parts
            if not graph['roots'][root] or (len(parts) == 2 and not parts[0].endswith(('.wants', '.requires', '.upholds'))):
                raise ValueError('Unit link is outside the captured directory layout')
            seen.add(item['path'])
    return record


def capture(host, names, output, *, with_dependencies=False):
    names = service_names(names)
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use an SSH hostname or user@hostname')
    source = Path(__file__).read_text()
    worker = ("import json,sys; scope={'__name__':'forge_target_service_worker'}; "
              'exec(' + repr(source) + ',scope); '
              "print(json.dumps(scope['capture_local'](json.load(sys.stdin), with_dependencies=" +
              repr(bool(with_dependencies)) + ')))')
    output = Path(output).absolute()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                                 'python3 -c ' + shlex.quote(worker)], input=json.dumps(names),
                                text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise ValueError('Target service capture failed: ' + result.stderr[-2000:])
        record = verify(json.loads(result.stdout), names)
        if with_dependencies and 'dependencies' not in record:
            raise ValueError('Target omitted required dependency observations')
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    verify(json.loads(output.read_text()), names)
    return {'status': 'captured', 'output': str(output), 'services': names}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--dependencies', action='store_true', help='Capture selected-unit dependency edges, not a transitive graph')
    cli.add_argument('services', nargs='+')
    args = cli.parse_args()
    print(json.dumps(capture(args.host, args.services, args.output, with_dependencies=args.dependencies)))


if __name__ == '__main__':
    main()
