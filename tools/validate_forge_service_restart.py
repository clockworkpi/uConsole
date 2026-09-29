#!/usr/bin/env python3
"""Prove the round-trip fixture's service executes again, without copying images."""
import argparse
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch
import uuid

from forge_runtime import Runtime
from forge_workspace import sha256
import uconsole_emulator as emulator
import validate_systemd_poweroff


def fixture_paths(token):
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid round-trip fixture identity')
    name = 'uconsole-forge-proof-' + token
    return {'name': name, 'app': '/usr/local/bin/' + name,
            'service': '/etc/systemd/system/' + name + '.service',
            'link': '/etc/systemd/system/multi-user.target.wants/' + name + '.service',
            'state': '/var/lib/' + name}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--roundtrip', type=Path, required=True, help='completed roundtrip.json')
    cli.add_argument('--module', type=Path, required=True)
    options = cli.parse_args()
    previous_path = options.roundtrip.resolve()
    previous = json.loads(previous_path.read_text())
    if previous.get('validation') != 'passed':
        raise ValueError('Requires a completed, passing image round-trip record')
    paths = fixture_paths(previous.get('identity'))
    token = previous['identity']
    candidate = options.module.resolve()
    if sha256(candidate) != previous['module_sha256']:
        raise ValueError('Candidate differs from the round-trip module')
    workspace = previous_path.parent / 'reimported'
    if sha256(workspace / 'base.img') != previous['export_sha256']:
        raise ValueError('Reimported base differs from the recorded exported image')
    evidence = {'validation': 'running', 'roundtrip': str(previous_path),
                'roundtrip_sha256': sha256(previous_path), 'identity': token,
                'export_sha256': previous['export_sha256'], 'commands': [],
                'physical_boot': 'unverified'}
    report = previous_path.parent / ('service-restart-' + uuid.uuid4().hex + '.json')
    runtime = None

    def execute(script):
        result = runtime.execute(script)
        evidence['commands'].append({'script': script, 'result': result})
        if result['exit_code']:
            raise ValueError(f'Guest verification failed: {result}')
        return result['stdout']

    def start():
        nonlocal runtime
        runtime = Runtime(emulator.parser().parse_args(
            ['--workspace', str(workspace), 'run', '--mode', 'maintenance']))
        runtime.start()
        emulator.wait_for_log(workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)

    def inspect():
        execute(f'test "$({paths["app"]})" = {token} && '
                f'test "$(readlink {paths["link"]})" = {paths["service"]} && '
                f'test -d {paths["state"]} && test ! -L {paths["state"]}')
        # The service and application must be identical across the reboot.
        return execute(f'sha256sum {paths["app"]} {paths["service"]}')

    try:
        start()
        before = inspect()
        result = paths['state'] + '/result'
        execute(f'test -f {result} && test ! -L {result} && rm -f {result} && test ! -e {result}')
        evidence['old_result_removed'] = True
        runtime.stop()
        with patch.object(sys, 'argv', ['validate_systemd_poweroff', '--workspace', str(workspace),
                                      '--module', str(candidate), '--expect-installed']):
            shutdown_report = validate_systemd_poweroff.main()
        shutdown = json.loads(shutdown_report.read_text())
        evidence['normal_shutdown'] = str(shutdown_report)
        if shutdown['validation'] != 'passed':
            raise ValueError('Installed-module normal shutdown failed')
        start()
        if inspect() != before:
            raise ValueError('Service or application changed during the reboot')
        execute(f'test -f {result} && test ! -L {result} && test "$(cat {result})" = {token}')
        evidence['fresh_result_verified'] = True
        runtime.stop()
        evidence['validation'] = 'passed'
    except BaseException as exc:
        evidence['validation'] = 'failed'
        evidence['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            if runtime and runtime.process is not None and runtime.process.poll() is None:
                evidence['forced_cleanup'] = True
                runtime.stop(force=True)
            if runtime:
                runtime.release()
        finally:
            with report.open('x') as destination:
                json.dump(evidence, destination, indent=2)
                destination.write('\n')
            print(f'Fresh service restart: {evidence["validation"]}; {report}', flush=True)


if __name__ == '__main__':
    main()
