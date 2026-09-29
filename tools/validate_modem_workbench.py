"""Real Workbench modem boot, GUI/MCP scenario changes and native guest queries."""
import argparse
import json
from pathlib import Path
import shutil
import time
import tempfile
import threading
import uuid
from unittest.mock import patch

from forge_workspace import sha256
from forge_filesystem import check_overlay_root
from validate_desktop_preparation_gui import fixture
from validate_audio_workbench import button, select_audio
from uconsole_mcp import Server, PROTOCOL


def query_script(registration):
    return "python3 - <<'PY'\n" + '''import os, select, time, tty, termios
fd = os.open('/dev/ttyUSB2', os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY)
try:
    tty.setraw(fd)
    termios.tcflush(fd, termios.TCIOFLUSH)
    def exchange(command):
        os.write(fd, command)
        response = bytearray()
        deadline = time.monotonic() + 10
        while not response.endswith(b'\\r\\nOK\\r\\n'):
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError('AT response deadline')
            response.extend(os.read(fd, 1024))
            if len(response) > 4096:
                raise RuntimeError('AT response too large')
        return bytes(response)
    echo = exchange(b'ATE0\\r')
    if echo not in (b'ATE0\\r\\r\\nOK\\r\\n', b'\\r\\nOK\\r\\n'):
        raise RuntimeError('Unexpected echo response')
    response = exchange(b'AT+CEREG?\\r')
    expected = b'\\r\\n+CEREG: 0,''' + str(registration) + '''\\r\\nOK\\r\\n'
    if response != expected:
        raise RuntimeError(repr(response))
    print('registration-verified')
finally:
    os.close(fd)
PY'''


def run(image, digest, output, *, external_client=False, usb_reset=False, usb_hotplug=False,
        hotplug_cycles=1):
    if type(hotplug_cycles) is not int or not 1 <= hotplug_cycles <= 20:
        raise ValueError('Hotplug cycles must be an integer from 1 to 20')
    if hotplug_cycles != 1 and not usb_hotplug:
        raise ValueError('Repeated cycles require USB hotplug')
    import tkinter as tk
    from uconsole_workbench import Workbench
    image, output = image.resolve(), output.resolve()
    if sha256(image) != digest:
        raise ValueError('Backing image hash mismatch')
    if shutil.disk_usage(output.parent).free < 512*1024**2:
        raise ValueError('Insufficient space for disposable validation')
    fixture(image, output, digest)
    socket_directory = tempfile.TemporaryDirectory(prefix='uc-modem-proof-')
    endpoint = Path(socket_directory.name)/'owner.sock'
    root = tk.Tk()
    app = runtime = None
    evidence = {'status':'failed', 'forced_cleanup':False}
    def wait(predicate, seconds):
        deadline = time.monotonic()+seconds
        while predicate():
            if time.monotonic() > deadline:
                raise TimeoutError('Workbench modem job deadline')
            root.update()
            time.sleep(0.02)
    try:
        with patch('uconsole_workbench.history_default_path', return_value=output/'jobs.sqlite3'):
            app = Workbench(root, output, agent_socket=endpoint if external_client else None,
                            agent_grants=['device-control'] if external_client else ())
            app.mode.set('maintenance')
            select_audio(root, app.modem, 'composite')
            button(root, 'Start').invoke()
        boot = app.boot_job
        if not boot:
            raise RuntimeError('Workbench did not submit modem boot')
        wait(lambda: app.boot_job is not None, 150)
        evidence['boot'] = app.controller.job(boot)
        runtime = app.runtime
        if evidence['boot']['status'] != 'completed' or runtime is None or runtime.modem is None:
            raise RuntimeError('Owned modem boot did not complete')
        def execute(script):
            app.release_serial()
            result = runtime.execute(script, timeout=30)
            if result['exit_code']:
                raise RuntimeError(str(result))
            return result
        evidence['setup'] = execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
            'mountpoint -q /sys || mount -t sysfs sysfs /sys; '
            'mountpoint -q /dev || mount -t devtmpfs devtmpfs /dev; modprobe option; modprobe rndis_host')
        button(root, 'Modem controls').invoke()
        panel = app.modem_panel
        panel.value.set('3')
        panel.apply.invoke()
        job = panel.job
        if not job:
            raise RuntimeError('Modem panel did not submit a job')
        wait(lambda: panel.job is not None, 30)
        evidence['gui_change'] = app.controller.job(job)
        if evidence['gui_change']['status'] != 'completed':
            raise RuntimeError('GUI modem change failed')
        evidence['guest_denied'] = execute(query_script(3))
        if external_client:
            from modem_mcp_validation import change
            results, failures = [], []
            def client():
                try:
                    results.append(change(endpoint, 5))
                except Exception as exc:
                    failures.append(str(exc))
            worker = threading.Thread(target=client, daemon=True)
            worker.start()
            wait(worker.is_alive, 90)
            worker.join()
            if failures:
                raise RuntimeError('External client failed: ' + str(failures))
            evidence['external_mcp'] = results[0]
            if app.runtime is not runtime or app.controller.runtimes['gui'] is not runtime:
                raise RuntimeError('External client changed runtime ownership')
            evidence['external_guest_roaming'] = execute(query_script(5))
        server = Server(app.controller)
        server.handle({'jsonrpc':'2.0', 'id':1, 'method':'initialize', 'params':{
            'protocolVersion':PROTOCOL, 'capabilities':{}, 'clientInfo':{'name':'modem-proof','version':'1'}}})
        server.handle({'jsonrpc':'2.0', 'method':'notifications/initialized'})
        response = server.handle({'jsonrpc':'2.0', 'id':2, 'method':'tools/call', 'params':{
            'name':'modem_set', 'arguments':{'workspace':'gui', 'registration':5}}})
        evidence['mcp_response'] = response
        if 'error' in response or response['result'].get('isError'):
            raise RuntimeError('MCP modem change rejected')
        pending = json.loads(response['result']['content'][0]['text'])
        job = pending['job_id']
        wait(lambda: app.controller.job(job)['status'] not in ('completed','failed','cancelled'), 30)
        evidence['mcp_change'] = app.controller.job(job)
        if evidence['mcp_change']['status'] != 'completed':
            raise RuntimeError('MCP modem job failed')
        evidence['guest_roaming'] = execute(query_script(5))
        from modem_pdp_probe import PROBE
        probe = output/'pdp-probe.py'
        probe.write_text(PROBE)
        guest_probe = '/tmp/forge-modem-pdp-' + uuid.uuid4().hex + '.py'
        app.release_serial()
        runtime.upload(probe, guest_probe)
        evidence['guest_pdp_execution'] = execute('python3 ' + guest_probe + ' > ' + guest_probe + '.json')
        runtime.download(guest_probe + '.json', output/'pdp-result.json')
        evidence['guest_pdp'] = json.loads((output/'pdp-result.json').read_text())
        if evidence['guest_pdp'].get('status') != 'passed':
            raise RuntimeError('Native guest PDP probe did not pass')
        execute('rm -- ' + guest_probe + ' ' + guest_probe + '.json')
        if usb_reset:
            from modem_usb_reset_probe import PROBE as RESET_PROBE
            reset_source = output/'usb-reset-probe.py'
            reset_source.write_text(RESET_PROBE)
            reset_guest = '/tmp/forge-modem-reset-' + uuid.uuid4().hex + '.py'
            runtime.upload(reset_source, reset_guest)
            evidence['usb_reset'] = execute('python3 ' + reset_guest)
            evidence['registration_after_reset'] = execute(query_script(5))
            runtime.upload(probe, guest_probe)
            execute('python3 ' + guest_probe + ' > ' + guest_probe + '.json')
            runtime.download(guest_probe + '.json', output/'pdp-after-reset.json')
            evidence['pdp_after_reset'] = json.loads((output/'pdp-after-reset.json').read_text())
            if evidence['pdp_after_reset'].get('status') != 'passed':
                raise RuntimeError('PDP operation after USB reset failed')
            execute('rm -- ' + reset_guest + ' ' + guest_probe + ' ' + guest_probe + '.json')
        if usb_hotplug:
            from modem_hotplug_traffic import PROBE as TRAFFIC_PROBE, validate_result
            traffic_source = output/'hotplug-traffic.py'
            traffic_source.write_text(TRAFFIC_PROBE)
            traffic_guest = '/tmp/forge-modem-traffic-' + uuid.uuid4().hex
            runtime.upload(traffic_source, traffic_guest + '.py')
            evidence['hotplug_cycles'] = hotplug_cycles
            for cycle, connected in ((n, state) for n in range(hotplug_cycles)
                                     for state in (False, True)):
                key = str(connected) if cycle == 0 else str(connected) + '_' + str(cycle)
                prefix = traffic_guest + '-' + str(cycle)
                if not connected:
                    execute('python3 ' + traffic_guest + '.py ' + prefix +
                            ' </dev/null >' + prefix + '.log 2>&1 &')
                    evidence['traffic_ready_' + str(cycle)] = execute(
                        "python3 - <<'PY'\nimport pathlib,time\np=pathlib.Path('" + prefix +
                        ".ready')\nend=time.monotonic()+5\nwhile not p.exists():\n"
                        " if time.monotonic()>end: raise RuntimeError('Traffic readiness deadline')\n"
                        " time.sleep(0.05)\nprint(p.read_text())\nPY")
                panel.cable_buttons[0 if connected else 1].invoke()
                job = panel.job
                if not job:
                    raise RuntimeError('Modem cable button did not submit a job')
                wait(lambda: panel.job is not None, 30)
                result = app.controller.job(job)
                evidence['usb_connected_' + key] = result
                if result['status'] != 'completed':
                    raise RuntimeError('USB attachment job failed')
                script = ("python3 - <<'PY'\nimport pathlib,time\nend=time.monotonic()+10\nwhile True:\n"
                          " serial=list(pathlib.Path('/sys/class/tty').glob('ttyUSB*'))\n"
                          " network=[p for p in pathlib.Path('/sys/class/net').iterdir() if (p/'device'/'driver').resolve().name=='rndis_host']\n"
                          f" if len(serial)=={5 if connected else 0} and len(network)=={1 if connected else 0}: break\n"
                          " if time.monotonic()>end: raise RuntimeError('USB enumeration deadline')\n"
                          " time.sleep(0.05)\nprint('enumeration-verified')\nPY")
                evidence['enumeration_' + key] = execute(script)
                if connected:
                    execute("python3 - <<'PY'\nimport pathlib,time\np=pathlib.Path('" + prefix +
                            ".json')\nend=time.monotonic()+15\nwhile not p.exists():\n"
                            " if time.monotonic()>end: raise RuntimeError('Traffic completion deadline')\n"
                            " time.sleep(0.05)\nPY")
                    traffic_result = output/('hotplug-traffic-' + str(cycle) + '.json')
                    runtime.download(prefix + '.json', traffic_result)
                    runtime.download(prefix + '.log', output/('hotplug-traffic-' + str(cycle) + '.log'))
                    result = json.loads(traffic_result.read_text())
                    validate_result(result)
                    evidence['traffic_' + str(cycle)] = result
                    evidence['registration_cycle_' + str(cycle)] = execute(query_script(5))
                    runtime.upload(probe, guest_probe)
                    execute('python3 ' + guest_probe + ' > ' + guest_probe + '.json')
                    cycle_result = output/('pdp-hotplug-cycle-' + str(cycle) + '.json')
                    runtime.download(guest_probe + '.json', cycle_result)
                    result = json.loads(cycle_result.read_text())
                    if result.get('status') != 'passed':
                        raise RuntimeError('PDP operation after hotplug cycle failed')
                    evidence['pdp_cycle_' + str(cycle)] = result
                    execute('rm -- ' + prefix + '.ready ' + prefix + '.json ' + prefix + '.log')
            evidence['registration_after_hotplug'] = execute(query_script(5))
            runtime.upload(probe, guest_probe)
            execute('python3 ' + guest_probe + ' > ' + guest_probe + '.json')
            runtime.download(guest_probe + '.json', output/'pdp-after-hotplug.json')
            evidence['pdp_after_hotplug'] = json.loads((output/'pdp-after-hotplug.json').read_text())
            if evidence['pdp_after_hotplug'].get('status') != 'passed':
                raise RuntimeError('PDP operation after USB hotplug failed')
            execute('rm -- ' + guest_probe + ' ' + guest_probe + '.json')
            execute('rm -- ' + traffic_guest + '.py')
        app.release_serial()
        runtime.stop()
        evidence['root_after_stop'] = check_overlay_root(output, runtime.args.qemu_img)
        evidence['base_unchanged'] = sha256(image) == digest
        if not evidence['base_unchanged'] or runtime.process.returncode != 0:
            raise RuntimeError('Clean shutdown/base integrity check failed')
        evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        if app is not None:
            app.release_serial()
            if app.controller:
                app.controller.executor.shutdown(wait=True)
                runtime = app.controller.runtimes.get('gui', runtime)
            if runtime and runtime.process and runtime.process.poll() is None:
                evidence['forced_cleanup'] = True
                runtime.stop(force=True)
            app.close_attachment()
        (output/'modem-workbench-acceptance.json').write_text(json.dumps(evidence, indent=2)+'\n')
        root.destroy()
        socket_directory.cleanup()
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--external-client', action='store_true')
    parser.add_argument('--usb-reset', action='store_true')
    parser.add_argument('--usb-hotplug', action='store_true')
    parser.add_argument('--hotplug-cycles', type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(run(args.image, args.sha256, args.output, external_client=args.external_client,
                         usb_reset=args.usb_reset, usb_hotplug=args.usb_hotplug,
                         hotplug_cycles=args.hotplug_cycles), indent=2))
