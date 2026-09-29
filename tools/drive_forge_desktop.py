#!/usr/bin/env python3
"""Drive the real desktop recorder with a bounded, repository-owned scenario.

For disposable fixtures only: unfinished guests are explicitly force-stopped by
the recorder at EOF. A completed scenario records actions, NOT a desktop pass.
Review screenshots and guest results separately. Never publish secrets.json or
guest disks from the recording directory.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def scenario(path):
    value = json.loads(path.read_text())
    if (not isinstance(value, dict) or set(value) != {'schema', 'steps'}
            or type(value['schema']) is not int or value['schema'] != 1):
        raise ValueError('Expected scenario schema 1 and steps')
    steps = value['steps']
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError('Expected 1..100 steps')
    allowed = {'status', 'capture', 'key', 'type', 'type-secret', 'move', 'click',
               'serial', 'serial-secret', 'start', 'finish', 'firmware', 'firmware-keys'}
    previous = 0
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {'at_seconds', 'request'}:
            raise ValueError('Each step requires at_seconds and request')
        at = step['at_seconds']
        request = step['request']
        if type(at) is not int or not previous <= at <= 1500:
            raise ValueError('Step times must be ordered integers in 0..1500')
        if not isinstance(request, dict) or request.get('action') not in allowed:
            raise ValueError('Unsupported recorder request')
        if request['action'] == 'finish' and index != len(steps) - 1:
            raise ValueError('finish must be the final step')
        previous = at
    return steps


def await_event(inbox, action, deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Recorder deadline expired waiting for ' + action)
        try:
            event = inbox.get(timeout=min(remaining, 1))
        except queue.Empty:
            continue
        if event is None:
            raise RuntimeError('Recorder exited before ' + action)
        if 'error' in event:
            raise RuntimeError('Recorder reported: ' + str(event))
        if event.get('action') == action:
            return event


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', type=Path, required=True)
    cli.add_argument('--scenario', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    options = cli.parse_args()
    steps = scenario(options.scenario)
    output = options.output.resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    evidence = {'status': 'failed', 'automated_desktop_pass': False,
                'events': [], 'scenario': {'schema': 1, 'steps': steps}}
    inbox = queue.Queue()
    process = None
    thread = None
    logs = contextlib.ExitStack()
    started = time.monotonic()
    deadline = started + 1800
    try:
        error_log = logs.enter_context((output / 'recorder.stderr.log').open('x'))
        event_log = logs.enter_context((output / 'recorder.stdout.log').open('x'))
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name('record_forge_desktop.py')),
            '--workspace', str(options.workspace.resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log, text=True,
            env=dict(os.environ), start_new_session=True)

        def reader():
            try:
                for line in process.stdout:
                    event_log.write(line)
                    event_log.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        inbox.put(event)
            finally:
                inbox.put(None)

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        ready = await_event(inbox, 'ready', min(deadline, time.monotonic() + 120))
        evidence['events'].append(ready)
        evidence['recording_directory'] = ready['evidence_directory']
        for step in steps:
            # These are intentional screenshot/input sampling times, not
            # a readiness predicate or evidence that a boot succeeded.
            due = started + step['at_seconds']
            while time.monotonic() < due:
                try:
                    process.wait(timeout=max(0.001, min(1, due - time.monotonic())))
                except subprocess.TimeoutExpired:
                    continue
                raise RuntimeError('Recorder exited before scheduled action')
            process.stdin.write(json.dumps(step['request']) + '\n')
            process.stdin.flush()
            if step['request']['action'] == 'finish':
                # Successful finish exits without a separate stdout response.
                # Its acknowledgement is the terminal recorder receipt below.
                break
            event = await_event(inbox, step['request']['action'],
                                min(deadline, time.monotonic() + 120))
            evidence['events'].append(event)
        process.stdin.close()
        process.wait(timeout=min(60, max(1, deadline - time.monotonic())))
        thread.join(timeout=5)
        if thread.is_alive() or process.returncode != 0:
            raise RuntimeError('Recorder did not exit cleanly')
        record = json.loads((Path(ready['evidence_directory']) / 'record.json').read_text())
        if steps[-1]['request']['action'] == 'finish' and (
                record['status'] != 'recorded' or record['forced_cleanup']):
            raise ValueError('finish did not acknowledge a cleanly stopped guest')
        evidence['recorder_status'] = record['status']
        evidence['forced_cleanup'] = record['forced_cleanup']
        evidence['status'] = 'scenario-recorded'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            # EOF gives the recorder its normal exact-owned-VM cleanup path.
            if not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                # Do not leave a hung Tk process or its owned VM on a CI host.
                import signal
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                evidence['forced_process_cleanup'] = True
        if thread is not None:
            thread.join(timeout=5)
        if process is not None:
            process.stdin.close()
            if thread is None or not thread.is_alive():
                process.stdout.close()
        logs.close()
        (output / 'driver.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Desktop scenario: ' + evidence['status'] + '; not an automated desktop pass')


if __name__ == '__main__':
    main()
