"""Bounded passive transitive systemd dependency capture, never an execution grant."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

from forge_target_services import DEPENDENCIES, parse_dependencies, service_names


def identity():
    return {'machine_id': Path('/etc/machine-id').read_text().strip(),
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def observe(name, timeout):
    result = subprocess.run(['systemctl', 'show', '--all', '--property=Id,' + ','.join(DEPENDENCIES), '--', name],
                            text=True, capture_output=True, timeout=timeout,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'})
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError('Dependency query failed or exceeded its output limit: ' + name)
    lines = result.stdout.splitlines()
    ids = [line for line in lines if line.startswith('Id=')]
    if ids != ['Id=' + name]:
        raise ValueError('Dependency alias or identity mismatch: ' + name)
    try:
        return parse_dependencies('\n'.join(line for line in lines if not line.startswith('Id=')))
    except ValueError as exc:
        raise ValueError('Invalid dependency properties for ' + name + ': ' + str(exc)) from exc


def capture_local(roots, *, max_units=512, timeout=120):
    roots = service_names(roots)
    if type(max_units) is not int or not len(roots) <= max_units <= 2048:
        raise ValueError('Dependency graph limit must cover roots and be at most 2048')
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise ValueError('Dependency capture timeout must be 1..300 seconds')
    started = identity()
    deadline = time.monotonic() + timeout

    def query(name):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Dependency capture deadline exceeded')
        return observe(name, min(10, remaining))

    nodes = {}
    pending = set(roots)
    while pending:
        name = min(pending)
        pending.remove(name)
        nodes[name] = query(name)
        pending.update(unit for units in nodes[name].values() for unit in units if unit not in nodes)
        if len(nodes) + len(pending) > max_units:
            raise ValueError('Dependency graph exceeds approved unit limit; no complete graph captured')
    # Traverse again in a fixed order, requiring the same edges at every node.
    # A changed edge is failure, not permission to silently widen the review.
    for name in sorted(nodes):
        if query(name) != nodes[name]:
            raise ValueError('Dependency graph changed during capture: ' + name)
    if identity() != started:
        raise ValueError('Target or boot changed during dependency capture')
    return dict(started, schema=1, roots=roots, nodes=nodes,
                scope='transitive closure of listed systemd dependency properties; not executable side effects',
                properties=list(DEPENDENCIES), complete=True, execution_approved=False)


def verify(record, roots):
    if (not isinstance(record, dict) or type(record.get('schema')) is not int or record['schema'] != 1 or
            record.get('roots') != service_names(roots) or record.get('complete') is not True or
            record.get('execution_approved') is not False or record.get('properties') != list(DEPENDENCIES) or
            not re.fullmatch('[0-9a-f]{32}', record.get('machine_id', '')) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', record.get('boot_id', '')) or
            not isinstance(record.get('nodes'), dict) or not 1 <= len(record['nodes']) <= 2048):
        raise ValueError('Invalid dependency graph record')
    nodes = record['nodes']
    reached = set()
    pending = set(roots)
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        if name not in nodes:
            raise ValueError('Dependency graph has an uncaptured edge')
        reached.add(name)
        values = nodes[name]
        if (not isinstance(values, dict) or set(values) != set(DEPENDENCIES) or
                any(not isinstance(units, list) or any(not isinstance(unit, str) for unit in units)
                    for units in values.values())):
            raise ValueError('Invalid graph adjacency')
        if parse_dependencies('\n'.join(key + '=' + ' '.join(units) for key, units in values.items())) != values:
            raise ValueError('Noncanonical graph adjacency')
        pending.update(unit for units in values.values() for unit in units if unit not in reached)
    if reached != set(nodes):
        raise ValueError('Dependency graph includes unreachable objects')
    return record


def capture(host, roots, output):
    roots = service_names(roots)
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use a literal SSH host')
    source = Path(__file__).resolve().parent
    request = {'roots': roots, 'modules': [(name, (source / (name + '.py')).read_text())
               for name in ('forge_target_services', 'forge_target_dependencies')]}
    worker = '''import json,sys,types
r=json.load(sys.stdin)
for name,source in r['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source, '<forge-owner:'+name+'>', 'exec'), module.__dict__)
print(json.dumps(sys.modules['forge_target_dependencies'].capture_local(r['roots'])))
'''
    output = Path(output).absolute()
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                                     'python3 -c ' + shlex.quote(worker)], input=json.dumps(request),
                                    text=True, capture_output=True, timeout=150)
            if result.returncode:
                raise ValueError('Remote dependency capture failed: ' + result.stderr[-2000:])
            record = verify(json.loads(result.stdout), roots)
        except BaseException as exc:
            json.dump({'complete': False, 'error': str(exc)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
            raise
        json.dump(record, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    parent = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    return {'status': 'captured', 'units': len(record['nodes']), 'output': str(output)}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', required=True)
    cli.add_argument('--output', required=True, type=Path)
    cli.add_argument('services', nargs='+')
    args = cli.parse_args()
    print(json.dumps(capture(args.host, args.services, args.output)))
