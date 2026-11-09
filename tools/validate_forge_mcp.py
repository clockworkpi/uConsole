#!/usr/bin/env python3
"""Real stdio MCP client acceptance test against a disposable prepared guest."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import tempfile
import time
import uuid

from uconsole_mcp import PROTOCOL
from forge_workspace import WorkspaceLock
from forge_replay import completed_event_records
from uconsole_emulator import wait_for_log


def ac_irq_counts(output):
    """Count only the AC driver's child IRQs, not shared GPIO/PMIC activity."""
    counts = {}
    for line in output.splitlines():
        if not line.rstrip().endswith('axp20x-ac-power-supply'):
            continue
        identifier, values = line.split(':', 1)
        total = 0
        for word in values.split():
            if not word.isdecimal():
                break
            total += int(word)
        counts[identifier.strip()] = total
    if len(counts) != 2:
        raise ValueError('Expected AC insertion and removal driver IRQ rows')
    return counts


def cancel_boot(call, workspace):
    """Observe an owned live VM before requesting cancellation over MCP."""
    job = call('boot', {'workspace': 'test', 'mode': 'maintenance'})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        state = call('workspace_inspect', {'workspace': 'test'})
        status = call('job_status', {'job_id': job['job_id']})
        if status['status'] in ('completed', 'failed', 'cancelled'):
            raise AssertionError(f'Boot became terminal before running cancellation: {status}')
        if state['runtime']['owned'] and state['runtime']['running']:
            break
        time.sleep(0.1)
    else:
        raise TimeoutError('Did not observe an owned running QEMU before cancellation')
    command = json.loads((workspace / 'last-command.json').read_text())
    address = command[command.index('-qmp') + 1]
    assert address.startswith('unix:'), address
    endpoint = Path(address[5:].split(',')[0])
    identity = command[command.index('-name') + 1]
    print(f'Observed owned running VM: {identity}', flush=True)
    result = call('job_cancel', {'job_id': job['job_id']})
    assert result['requested'], result
    while time.monotonic() < deadline:
        status = call('job_status', {'job_id': job['job_id']})
        if status['status'] in ('completed', 'failed', 'cancelled'):
            break
        time.sleep(0.1)
    assert status['status'] == 'cancelled', status
    assert 'filesystem may be unclean' in status['detail'], status
    state = call('workspace_inspect', {'workspace': 'test'})
    assert state['runtime']['owned'] and state['runtime']['running'] is False, state
    assert not endpoint.parent.exists(), endpoint
    with WorkspaceLock(workspace):
        pass
    print('PASS: real MCP running-boot cancellation, owned QEMU exit, private endpoint cleanup '
          'and workspace-lock release. Forced stop is not clean-shutdown evidence.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    scenarios = parser.add_mutually_exclusive_group()
    scenarios.add_argument('--adc', action='store_true',
                           help='Test ADC voltage, power loss and replay through MCP and the stock guest driver')
    scenarios.add_argument('--keyboard', action='store_true',
                           help='Test persistent firmware input through real MCP and Linux evdev')
    scenarios.add_argument('--initial-power', action='store_true',
                           help='Test initial power profile, Linux readings and durable boot evidence')
    scenarios.add_argument('--cancel-boot', action='store_true',
                        help='Test running-boot cancellation instead of the round trip; '
                             'forces power removal and may leave the disposable guest unclean')
    scenarios.add_argument('--timeout-guest', action='store_true',
                           help='Test guest deadline termination, channel reuse and clean stop')
    scenarios.add_argument('--cancel-guest', action='store_true',
                           help='Test cancellation after observing a guest process group, then clean stop')
    scenarios.add_argument('--cancel-upload', action='store_true',
                           help='Cancel after acknowledged upload chunks; verify original destination and clean stop')
    scenarios.add_argument('--cancel-download', action='store_true',
                           help='Cancel an in-flight download; prevent host publication and verify channel reuse')
    scenarios.add_argument('--host-task-policy-test', action='store_true',
                           help='Test a pinned harmless host-task fixture without booting QEMU')
    parser.add_argument('--adc-reference', choices=['fixed', 'missing'], default='fixed')
    args = parser.parse_args()
    if args.adc_reference != 'fixed' and not args.adc:
        parser.error('--adc-reference requires --adc')
    log_path = args.workspace / ('mcp-client-' + uuid.uuid4().hex + '.log')
    transcript_path = log_path.with_suffix('.jsonl')
    history_path = args.workspace.resolve() / '.mcp-history/jobs.sqlite3'
    with tempfile.TemporaryDirectory(prefix='uc-mcp-files-') as directory, \
         log_path.open('x') as log, transcript_path.open('x') as transcript:
        grants = ['--allow', 'boot', '--allow', 'force-stop', '--allow', 'guest-exec', '--allow', 'transfer']
        if args.initial_power or args.keyboard or args.adc:
            grants += ['--allow', 'device-control']
        if args.host_task_policy_test:
            policy = Path(directory) / 'host-policy.json'
            policy.write_text(json.dumps({'schema': 1, 'tasks': {'fixture': {
                'workspace': 'test', 'cwd': directory, 'timeout': 10,
                'argv': [str(Path(sys.executable).resolve()), '-c',
                         'import os; print("approved host fixture"); print(os.getcwd())']}}}))
            policy_hash = hashlib.sha256(policy.read_bytes()).hexdigest()
            grants = ['--allow', 'host-task', '--host-task-policy', str(policy),
                      '--host-task-policy-sha256', policy_hash]
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
            '--workspace', f'test={args.workspace.resolve()}', '--files-root', directory,
            '--history', str(history_path),
            *grants],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1)
        next_id = 0
        outcomes = {}

        def request(method, params=None, notification=False, expected_error=None):
            nonlocal next_id
            next_id += 1
            message = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
            if not notification:
                message['id'] = next_id
            transcript.write(json.dumps({'direction': 'request', 'message': message}) + '\n')
            transcript.flush()
            process.stdin.write(json.dumps(message) + '\n')
            process.stdin.flush()
            if notification:
                return None
            if not select.select([process.stdout], [], [], 15)[0]:
                raise TimeoutError(f'No MCP reply for {method}; see {log_path}')
            reply = json.loads(process.stdout.readline())
            transcript.write(json.dumps({'direction': 'response', 'message': reply}) + '\n')
            transcript.flush()
            assert reply.get('id') == next_id, reply
            if expected_error is not None:
                assert reply.get('error', {}).get('code') == expected_error, reply
                return reply
            assert 'error' not in reply, reply
            return reply['result']

        def call(name, arguments):
            result = request('tools/call', {'name': name, 'arguments': arguments})
            assert not result.get('isError'), result
            content = result['structuredContent']
            if name == 'job_status' and content['status'] in ('completed', 'failed', 'cancelled'):
                outcomes[content['job_id']] = content
            return content

        def terminal(result):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                status = call('job_status', {'job_id': result['job_id']})
                if status['status'] in ('completed', 'failed', 'cancelled'):
                    return status
                time.sleep(0.1)
            raise TimeoutError(f'MCP job still active: {result}')

        def job(name, arguments):
            status = terminal(call(name, {'workspace': 'test', **arguments}))
            assert status['status'] == 'completed', status
            return status['result']

        try:
            result = request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                            'clientInfo': {'name': 'forge-acceptance', 'version': '1'}})
            assert result['protocolVersion'] == PROTOCOL
            request('notifications/initialized', notification=True)
            assert request('tools/list')['tools']
            assert request('resources/read', {'uri': 'forge://workspace/test/state'})['contents']
            denied = request('tools/call', {'name': 'export', 'arguments': {'workspace': 'test', 'host_path': 'denied.img'}})
            assert denied['isError']
            if args.adc:
                from validate_adc101c_guest import READER
                from forge_filesystem import check_overlay_root
                from forge_workspace import sha256
                from uconsole_emulator import executable
                before = sha256(args.workspace / 'base.img')
                job('boot', {'mode': 'maintenance', 'adc_reference': args.adc_reference})
                setup = job('guest_exec', {'script':
                    'set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                    'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                    'modprobe i2c_bcm2835; modprobe ti_adc081c'})
                assert setup['exit_code'] == 0, setup
                samples = []

                def sample():
                    result = job('guest_exec', {'script': 'python3 -c ' + shlex.quote(READER)})
                    assert result['exit_code'] == 0, result
                    observed = json.loads(next(line.split(':', 1)[1]
                        for line in result['stdout'].splitlines() if line.startswith('ADC_SAMPLE:')))
                    samples.append(observed)
                    return observed

                baseline = job('power_query', {})['power']
                for voltage, expected in ((0, 0), (1650000, 512), (3300000, 1023)):
                    changed = job('power_set', {'adc_input_uv': voltage})
                    assert changed['observed']['adc_input_uv'] == voltage, changed
                    assert changed['observed']['battery_voltage_uv'] == baseline['battery_voltage_uv'], changed
                    observed = sample()
                    assert observed['reference_supply_present'] == (args.adc_reference == 'fixed'), observed
                    assert observed['readings'][-1] == expected, observed
                    if args.adc_reference == 'missing':
                        assert observed['scale'] == {'errno': 22}, observed
                    else:
                        assert abs(float(observed['scale']) - 3300 / 1024) < 1e-8, observed
                job('power_set', {'adc_powered': False})
                assert sample()['readings'] == [{'errno': 5}] * 3, samples
                schedule = Path(directory) / 'adc-recovery.json'
                schedule.write_text(json.dumps({'schema': 1, 'events': [
                    {'at_ms': 0, 'power': {'adc_input_uv': 1650000}},
                    {'at_ms': 10, 'power': {'adc_powered': True}}]}) + '\n')
                replay = job('power_replay', {'schedule_path': schedule.name})
                assert replay['completed_events'] == 2, replay
                assert sample()['readings'][-1] == 512, samples
                job('stop', {})
                filesystem = check_overlay_root(args.workspace, executable('qemu-img'))
                assert sha256(args.workspace / 'base.img') == before
                transcript.write(json.dumps({'adc_observed': samples,
                    'root_after_stop': filesystem, 'base_unchanged': True,
                    'scope': 'stdio MCP, stock guest driver; not physical equivalence or virtual-time replay'}) + '\n')
                transcript.flush()
                print('PASS: MCP ADC inputs, guest conversions, power-loss EIO, replay recovery and unchanged base', flush=True)
            elif args.keyboard:
                from validate_keyboard_guest import verify_firmware_events
                from forge_filesystem import check_overlay_root
                from forge_workspace import sha256
                from uconsole_emulator import executable
                before = sha256(args.workspace / 'base.img')
                job('boot', {'mode': 'maintenance', 'keyboard': 'composite'})
                setup = job('guest_exec', {'script':
                    'set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                    'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
                    'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; '
                    'modprobe usbhid; modprobe cdc_acm'})
                assert setup['exit_code'] == 0, setup
                source = Path(directory) / 'keyboard-probe.py'
                source.write_bytes(Path(__file__).with_name('keyboard_guest_probe.py').read_bytes())
                token = uuid.uuid4().hex
                guest = '/tmp/keyboard-mcp-' + token
                ready, done = 'UC_KEYBOARD_READY_' + token, 'UC_KEYBOARD_DONE_' + token
                job('upload', {'host_path': source.name, 'guest_path': guest + '.py'})
                # This bounded, disposable observer is separate from the job process
                # group so serial workspace jobs can deliver input while it watches.
                observer = (f'python3 {guest}.py --profile firmware --ready {ready} '
                            f'> {guest}.out 2> {guest}.err; echo $? > {guest}.rc; '
                            f'printf "\\n{done}\\n" > /dev/ttyAMA1')
                launch = ('import subprocess; subprocess.Popen(' + repr(['sh', '-c', observer]) +
                          ', start_new_session=True, stdin=subprocess.DEVNULL, '
                          'stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)')
                started = job('guest_exec', {'script': 'python3 -c ' + shlex.quote(launch)})
                assert started['exit_code'] == 0, started
                wait_for_log(args.workspace / 'serial.log', ready.encode(), process, 25)
                for commands in (
                        [['matrix', 4, 2, 1], ['run', 10]],
                        [['matrix', 4, 2, 0], ['run', 10]],
                        [['matrix', 7, 2, 1], ['run', 10]],
                        [['matrix', 1, 0, 1], ['run', 10]],
                        [['matrix', 7, 2, 0], ['run', 10]],
                        [['matrix', 1, 0, 0], ['run', 10]]):
                    result = job('keyboard_input', {'commands': commands})
                    assert result['status'] == 'completed', result
                wait_for_log(args.workspace / 'serial.log', done.encode(), process, 35)
                observed = job('guest_exec', {'script':
                    f'test "$(cat {guest}.rc)" = 0 && cat {guest}.out'})
                assert observed['exit_code'] == 0, observed
                captured = json.loads(observed['stdout'])
                verify_firmware_events(captured)
                assert captured['drivers'] == ['usbhid', 'cdc_acm', 'cdc_acm'], captured
                cleanup = job('guest_exec', {'script':
                    f'rm -- {guest}.py {guest}.out {guest}.err {guest}.rc'})
                assert cleanup['exit_code'] == 0, cleanup
                job('stop', {})
                filesystem = check_overlay_root(args.workspace, executable('qemu-img'))
                assert sha256(args.workspace / 'base.img') == before
                transcript.write(json.dumps({'keyboard_observed': captured,
                                             'root_after_stop': filesystem,
                                             'base_unchanged': True}) + '\n')
                transcript.flush()
                print('PASS: real MCP firmware actions, ordered Linux evdev, clean stop and unchanged base', flush=True)
            elif args.host_task_policy_test:
                listing = call('host_tasks', {'workspace': 'test'})
                assert listing['execution_granted'] and listing['tasks'][0]['policy_sha256'] == policy_hash
                for _ in range(2):
                    result = job('host_task', {'task': 'fixture'})
                    assert result['exit_code'] == 0 and result['policy_sha256'] == policy_hash, result
                    assert result['stdout'] == 'approved host fixture\n' + directory + '\n', result
                    policy.write_text('{"schema":1,"tasks":{}}')
                rejected = request('tools/call', {'name': 'host_task', 'arguments': {
                    'workspace': 'test', 'task': 'fixture', 'argv': ['/bin/sh']}}, expected_error=-32602)
                assert rejected['error']['code'] == -32602, rejected
                rejected = request('tools/call', {'name': 'host_task', 'arguments': {
                    'workspace': 'test', 'task': 'unapproved'}})
                assert rejected['isError'], rejected
                state = call('workspace_inspect', {'workspace': 'test'})
                assert not state['runtime']['owned'], state
                print('PASS: pinned host-task execution, immutable startup snapshot, rejected overrides and no VM launch', flush=True)
            elif args.initial_power:
                profile = Path(directory) / 'power.json'
                profile.write_text(json.dumps({'schema': 1, 'power': {
                    'ac_present': False, 'battery_present': True,
                    'battery_voltage_uv': 3300000, 'battery_current_ma': -250,
                    'battery_capacity': 25, 'power_key_pressed': False}}))
                expected_hash = hashlib.sha256(profile.read_bytes()).hexdigest()
                boot = job('boot', {'mode': 'maintenance', 'scenario_path': 'power.json'})
                evidence = boot['scenario']
                assert evidence['source_sha256'] == expected_hash, evidence
                assert evidence['runtime_identity'] == boot['identity'], evidence
                assert evidence['requested'] == evidence['observed'], evidence
                paused = job('pause', {})
                assert paused['observed'] == {'status': 'paused', 'running': False}, paused
                resumed = job('resume', {})
                assert resumed['observed'] == {'status': 'running', 'running': True}, resumed
                result = job('guest_exec', {'script':
                    'mount -t proc proc /proc; mount -t sysfs sysfs /sys; '
                    'modprobe i2c_bcm2835; modprobe axp20x_i2c; '
                    'modprobe axp20x_ac_power; modprobe axp20x_adc; modprobe axp20x_battery; '
                    'v=$(cat /sys/class/power_supply/axp22x-ac/online '
                    '/sys/class/power_supply/axp20x-battery/{present,voltage_now,current_now,capacity}) || exit; '
                    'printf "UC_PROFILE:%s\\n" "$v"'})
                assert result['exit_code'] == 0, result
                assert 'UC_PROFILE:0\n1\n3300000\n-250000\n25\n' in result['stdout'], result
                before = job('power_query', {})
                assert before['power']['ac_present'] is False, before
                changed = job('power_set', {'ac_present': True})
                assert changed['before']['ac_present'] is False, changed
                assert changed['observed']['ac_present'] is True, changed
                result = job('guest_exec', {'script': 'cat /sys/class/power_supply/axp22x-ac/online'})
                assert result['exit_code'] == 0 and result['stdout'].strip() == '1', result
                assert job('power_query', {})['power']['ac_present'] is True
                irq_before = job('guest_exec', {'script': 'cat /proc/interrupts'})
                assert irq_before['exit_code'] == 0, irq_before
                irq_before = ac_irq_counts(irq_before['stdout'])
                schedule = Path(directory) / 'cycle.json'
                schedule.write_text(json.dumps({'schema': 1, 'events': [
                    {'at_ms': 0, 'power': {'ac_present': False}},
                    {'at_ms': 1000, 'power': {'ac_present': True}}]}))
                replay = job('power_replay', {'schedule_path': 'cycle.json'})
                assert replay['completed_events'] == 2, replay
                events = [json.loads(line) for line in Path(replay['evidence_path']).read_text().splitlines()]
                completed = completed_event_records(events)
                assert [event['result']['observed']['ac_present'] for event in completed] == [False, True], events
                assert completed[1]['elapsed_ms'] >= 1000, events
                result = job('guest_exec', {'script': 'cat /sys/class/power_supply/axp22x-ac/online'})
                assert result['exit_code'] == 0 and result['stdout'].strip() == '1', result
                irq_after = job('guest_exec', {'script': 'cat /proc/interrupts'})
                assert irq_after['exit_code'] == 0, irq_after
                irq_after = ac_irq_counts(irq_after['stdout'])
                assert irq_after.keys() == irq_before.keys(), (irq_before, irq_after)
                assert all(irq_after[key] > value for key, value in irq_before.items()), (irq_before, irq_after)

                # Cancel only after the real model confirms the first event.
                # A distant second deadline makes this a running cancellation,
                # not a race against a naturally completed short schedule.
                schedule.write_text(json.dumps({'schema': 1, 'events': [
                    {'at_ms': 0, 'power': {'ac_present': False}},
                    {'at_ms': 300000, 'power': {'ac_present': True}}]}))
                pending = call('power_replay', {'workspace': 'test', 'schedule_path': 'cycle.json'})
                state = call('job_status', {'job_id': pending['job_id']})
                replay_log = Path(state['context']['evidence_path'])
                wait_for_log(replay_log, b'"event": 0, "status": "completed"', process, 30)
                assert call('job_cancel', {'job_id': pending['job_id']})['requested']
                cancelled = terminal(pending)
                assert cancelled['status'] == 'cancelled', cancelled
                events = [json.loads(line) for line in replay_log.read_text().splitlines()]
                assert events[-1]['status'] == 'cancelled' and events[-1]['completed_events'] == 1, events
                assert [event['event'] for event in events if event.get('status') == 'dispatching'] == [0], events
                assert job('power_query', {})['power']['ac_present'] is False
                result = job('guest_exec', {'script': 'cat /sys/class/power_supply/axp22x-ac/online'})
                assert result['exit_code'] == 0 and result['stdout'].strip() == '0', result
                job('stop', {})
                print('PASS: MCP power snapshot, replay driver IRQs, running replay cancellation, '
                      'preserved partial effect and clean stop', flush=True)
            elif args.cancel_boot:
                cancel_boot(call, args.workspace)
            elif args.timeout_guest:
                job('boot', {'mode': 'maintenance'})
                result = job('guest_exec', {'script': 'trap "" TERM; sleep 30 & wait', 'timeout': 1})
                assert result['exit_code'] == 124 and result['timed_out'], result
                assert result['process_group_terminated'], result
                assert job('guest_exec', {'script': 'true'})['exit_code'] == 0
                job('stop', {})
                print('PASS: real guest deadline, process-group termination, serial reuse and clean stop', flush=True)
            elif args.cancel_upload:
                job('boot', {'mode': 'maintenance'})
                guest = '/tmp/uconsole-upload-cancel-' + uuid.uuid4().hex
                staging = "find /tmp -maxdepth 1 -name 'uconsole-agent-*' -print | sort"
                initial = job('guest_exec', {'script': f'printf preserved > {guest}; {staging}'})
                assert initial['exit_code'] == 0, initial
                (Path(directory) / 'large.bin').write_bytes(b'x' * (8 * 1024 * 1024))

                def acknowledgements():
                    text = request('resources/read', {'uri': 'forge://workspace/test/serial'})['contents'][0]['text']
                    return set(re.findall(r'UC_AGENT_([0-9a-f]{32})_END:0', text))

                before = acknowledgements()
                pending = call('upload', {'workspace': 'test', 'host_path': 'large.bin', 'guest_path': guest})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    seen = acknowledgements() - before
                    status = call('job_status', {'job_id': pending['job_id']})
                    assert status['status'] in ('running', 'queued'), status
                    # Upload first checks uid, creates staging, then writes
                    # chunks. Four new acknowledgements allow for one late
                    # log write from prior cleanup and still prove this is
                    # not merely a prelaunch/queued cancellation test.
                    if len(seen) >= 4:
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError('Did not observe acknowledged upload chunks')
                print(f'Observed {len(seen)} acknowledged upload transactions', flush=True)
                assert call('job_cancel', {'job_id': pending['job_id']})['requested']
                status = terminal(pending)
                assert status['status'] == 'cancelled', status
                job('download', {'guest_path': guest, 'host_path': 'unchanged.txt'})
                assert (Path(directory) / 'unchanged.txt').read_bytes() == b'preserved'
                after = job('guest_exec', {'script': f'rm -f {guest}; {staging}'})
                assert after['exit_code'] == 0 and after['stdout'] == initial['stdout'], after
                job('stop', {})
                print('PASS: real upload cancellation after chunk acknowledgement, preserved destination, '
                      'staging cleanup, serial reuse and clean stop', flush=True)
            elif args.cancel_download:
                job('boot', {'mode': 'maintenance'})
                guest = '/tmp/uconsole-download-cancel-' + uuid.uuid4().hex
                created = job('guest_exec', {'script': f'dd if=/dev/zero of={guest} bs=1048576 count=4 status=none'})
                assert created['exit_code'] == 0, created
                target = Path(directory) / 'cancelled.bin'
                # Read new console bytes directly: the MCP resource is a
                # bounded tail and can legitimately lose the BEGIN marker
                # while a large download is streaming.
                with (args.workspace / 'serial.log').open() as serial:
                    serial.seek(0, 2)
                    pending = call('download', {'workspace': 'test', 'host_path': target.name,
                                               'guest_path': guest})
                    deadline = time.monotonic() + 60
                    observed = ''
                    while time.monotonic() < deadline:
                        observed += serial.read()
                        status = call('job_status', {'job_id': pending['job_id']})
                        assert status['status'] in ('queued', 'running'), status
                        if re.search(r'UC_DATA_[0-9a-f]{32}:A{128}', observed):
                            break
                        observed = observed[-4096:]
                        time.sleep(0.01)
                    else:
                        raise TimeoutError('Did not observe an in-flight download payload')
                assert call('job_cancel', {'job_id': pending['job_id']})['requested']
                status = terminal(pending)
                assert status['status'] == 'cancelled', status
                assert not target.exists(), target
                assert job('guest_exec', {'script': f'rm -f {guest}; true'})['exit_code'] == 0
                job('stop', {})
                print('PASS: real in-flight download cancellation, no host publication, '
                      'serial reuse and clean stop', flush=True)
            elif args.cancel_guest:
                job('boot', {'mode': 'maintenance'})
                pending = call('guest_exec', {'workspace': 'test',
                               'script': 'trap "" TERM; sleep 60 & wait', 'timeout': 120})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    state = call('workspace_inspect', {'workspace': 'test'})
                    status = call('job_status', {'job_id': pending['job_id']})
                    assert status['status'] in ('running', 'queued'), status
                    if state.get('guest_job'):
                        assert state['guest_job']['process_group'] > 1, state
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError('Did not observe a running guest process group')
                print(f'Observed guest job: {state["guest_job"]}', flush=True)
                assert call('job_cancel', {'job_id': pending['job_id']})['requested']
                status = terminal(pending)
                assert status['status'] == 'cancelled', status
                assert 'Guest process group terminated' in status['detail'], status
                assert job('guest_exec', {'script': 'true'})['exit_code'] == 0
                job('stop', {})
                print('PASS: real running guest-job cancellation, serial reuse and clean stop', flush=True)
            else:
                job('boot', {'mode': 'maintenance'})
                result = job('guest_exec', {'script': 'id -u; uname -m'})
                assert result['exit_code'] == 0 and 'aarch64' in result['stdout'], result
                source = Path(directory) / 'source.txt'
                source.write_text('MCP guest round trip\n')
                guest = '/tmp/uconsole-mcp-' + uuid.uuid4().hex
                job('upload', {'host_path': 'source.txt', 'guest_path': guest})
                job('download', {'host_path': 'download.txt', 'guest_path': guest})
                assert (Path(directory) / 'download.txt').read_bytes() == source.read_bytes()
                job('screenshot', {'host_path': 'guest.png'})
                assert (Path(directory) / 'guest.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
                job('guest_exec', {'script': f'rm -f {guest}'})
                job('stop', {})
                print('PASS: real MCP handshake, permissions, async boot, guest exec, transfer, capture and clean stop')
        finally:
            process.stdin.close()
            process.wait(timeout=180)
            process.stdout.close()
        assert process.returncode == 0, log_path.read_text()
        # Reconnect with no grants. History access must not adopt the previous
        # runtime or authorize cancellation/mutations in the new session.
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name('uconsole_mcp.py')),
            '--workspace', f'test={args.workspace.resolve()}', '--history', str(history_path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1)
        try:
            request('initialize', {'protocolVersion': PROTOCOL, 'capabilities': {},
                                   'clientInfo': {'name': 'forge-history-reader', 'version': '1'}})
            request('notifications/initialized', notification=True)
            state = call('workspace_inspect', {'workspace': 'test'})
            assert not state['runtime']['owned'] and state['grants'] == [], state
            listing = call('job_history', {'workspace': 'test', 'limit': 100})
            assert set(outcomes) <= {item['job_id'] for item in listing['jobs']}, listing
            for job_id, expected in list(outcomes.items()):
                actual = call('job_status', {'job_id': job_id})
                assert actual['historical'] and actual['status'] == expected['status'], actual
                if 'result' in expected:
                    assert actual['result'] == expected['result'], actual
                if 'context' in expected:
                    assert actual['context'] == expected['context'], actual
                denied = request('tools/call', {'name': 'job_cancel', 'arguments': {'job_id': job_id}})
                assert denied['isError'], denied
            print('PASS: durable results after read-only reconnect, no VM adoption or historical cancellation', flush=True)
        finally:
            process.stdin.close()
            process.wait(timeout=30)
            process.stdout.close()
        assert process.returncode == 0, log_path.read_text()
        transcript.write(json.dumps({'validation': 'passed', 'server_exit_code': process.returncode,
                                     'scenario': 'keyboard' if args.keyboard else 'initial-power' if args.initial_power else
                                     ('host-task' if args.host_task_policy_test else
                                     ('cancel-boot' if args.cancel_boot else
                                     ('timeout-guest' if args.timeout_guest else
                                      ('cancel-guest' if args.cancel_guest else
                                       ('cancel-upload' if args.cancel_upload else
                                        ('cancel-download' if args.cancel_download else 'round-trip'))))))}) + '\n')
        transcript.flush()
        print(f'MCP transcript: {transcript_path}', flush=True)


if __name__ == '__main__':
    main()
