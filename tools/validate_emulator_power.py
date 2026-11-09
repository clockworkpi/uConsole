#!/usr/bin/env python3
"""Qualify PMIC AC events through the official guest drivers in a disposable image."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import time
import uuid

from forge_runtime import Runtime
from forge_scenario import Scenario, change_power
from uconsole_emulator import parser, wait_for_log


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--workspace', required=True, type=Path)
    cli.add_argument('--scenario', type=Path, help='Also verify initial scenario state in the guest')
    options = cli.parse_args()
    args = parser().parse_args(['--workspace', str(options.workspace), 'run', '--mode', 'maintenance'])
    args.scenario = options.scenario
    scenario = Scenario.load(options.scenario) if options.scenario else None
    evidence = options.workspace / ('power-client-' + uuid.uuid4().hex + '.jsonl')
    with evidence.open('x') as transcript:
        def record(kind, value):
            transcript.write(json.dumps({'kind': kind, 'value': value}) + '\n')
            transcript.flush()

        runtime = Runtime(args).start()
        try:
            wait_for_log(runtime.workspace / 'serial.log', b'root@(none):/#', runtime.process, 120)

            def execute(script):
                result = runtime.execute(script)
                record('guest', result)
                assert result['exit_code'] == 0, result
                return result['stdout']

            execute('set -e; mountpoint -q /proc || mount -t proc proc /proc; '
                    'mountpoint -q /sys || mount -t sysfs sys /sys; '
                    'modprobe i2c_bcm2835; modprobe axp20x_i2c; modprobe axp20x_ac_power; '
                    'modprobe axp20x_adc; modprobe axp20x_battery; modprobe evdev')
            children = runtime.control('qom-list', {'path': '/machine/unattached'})
            pmics = [item for item in children if item['type'] == 'child<axp221_pmu>']
            assert len(pmics) == 1, children
            pmic = '/machine/unattached/' + pmics[0]['name']
            temperature_reader = '''import json
from pathlib import Path
devices = [p for p in Path('/sys/bus/iio/devices').glob('iio:device*')
           if (p/'of_node/compatible').is_file()
           and b'x-powers,axp221-adc' in (p/'of_node/compatible').read_bytes().split(bytes([0]))
           and (p/'in_temp_raw').is_file()]
assert len(devices) == 1, {str(p): sorted(q.name for q in p.iterdir())
    for p in Path('/sys/bus/iio/devices').glob('iio:device*')}
p = devices[0]
print('UC_TEMP:' + json.dumps(dict(device=str(p),
    raw=int((p/'in_temp_raw').read_text()),
    offset=int((p/'in_temp_offset').read_text()),
    scale=int((p/'in_temp_scale').read_text()))))
'''
            for temperature in (25000, -5101, 85099):
                changed = change_power(runtime.control, 'pmic_temperature_mc', temperature)
                record('temperature-control', changed)
                output = execute('python3 -c ' + shlex.quote(temperature_reader))
                observed = json.loads(next(line.split(':', 1)[1] for line in output.splitlines()
                                           if line.startswith('UC_TEMP:')))
                assert observed['offset'] == -2677 and observed['scale'] == 100, observed
                assert (observed['raw'] + observed['offset']) * observed['scale'] == \
                    temperature // 100 * 100, observed
                record('guest-pmic-temperature', observed)
            change_power(runtime.control, 'pmic_temperature_mc',
                         scenario.power.pmic_temperature_mc if scenario else 25000)
            snapshot = (
                'import json; from pathlib import Path; '
                'p=Path("/sys/class/power_supply/axp22x-ac"); '
                'print("UC_POWER:"+json.dumps({"online":int((p/"online").read_text()), '
                '"present":int((p/"present").read_text()), '
                '"interrupts":[s for s in Path("/proc/interrupts").read_text().splitlines() '
                'if "acin" in s.lower() or "axp" in s.lower()]}))')

            def sample():
                output = execute('python3 -c ' + shlex.quote(snapshot))
                return json.loads(next(line.split(':', 1)[1] for line in output.splitlines()
                                       if line.startswith('UC_POWER:')))

            def irq_count(state):
                count = 0
                for line in state['interrupts']:
                    if not line.rstrip().endswith('axp20x-ac-power-supply'):
                        continue  # Parent GPIO/PMIC activity is not driver acceptance.
                    for word in line.split(':', 1)[1].split():
                        if not word.isdecimal():
                            break
                        count += int(word)
                return count

            before = sample()
            initial_ac = scenario.power.ac_present if scenario else True
            assert before['online'] == before['present'] == int(initial_ac), before
            if scenario:
                applied = json.loads((runtime.workspace / f'scenario-{runtime.identity}.json').read_text())
                assert applied['source_sha256'] == scenario.sha256, applied
                record('initial-scenario', applied)
                initial = execute('cat /sys/class/power_supply/axp20x-battery/'
                                  '{present,voltage_now,current_now,capacity}').strip().splitlines()
                power = scenario.power
                assert initial == [str(int(power.battery_present)),
                                   str((power.battery_voltage_uv // 1100 * 1100) // 1000 * 1000),
                                   str(power.battery_current_ma * 1000),
                                   str(power.battery_capacity if power.battery_present else 100)], initial
            for present in (not initial_ac, initial_ac):
                runtime.control('qom-set', {'path': pmic, 'property': 'ac-present', 'value': present})
                record('ac-present', present)
                deadline = time.monotonic() + 15
                while True:
                    after = sample()
                    if (after['online'] == after['present'] == int(present)
                            and irq_count(after) > irq_count(before)):
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError({'before': before, 'after': after, 'expected': present})
                    time.sleep(0.1)
                before = after
            runtime.control('qom-set', {'path': pmic, 'property': 'ac-present', 'value': True})
            for present in (False, True, False):
                runtime.control('qom-set', {'path': pmic, 'property': 'battery-present',
                                            'value': present})
                record('battery-present', present)
                value = execute('cat /sys/class/power_supply/axp20x-battery/present').strip()
                assert value == str(int(present)), value
                if present:
                    for voltage in (3300000, 4400000):
                        runtime.control('qom-set', {'path': pmic, 'property': 'battery-voltage-uv',
                                                    'value': voltage})
                        record('battery-voltage-uv', voltage)
                        value = execute('cat /sys/class/power_supply/axp20x-battery/voltage_now').strip()
                        assert value == str(voltage), value
                    def battery_value(prop, value):
                        runtime.control('qom-set', {'path': pmic, 'property': prop, 'value': value})
                        record(prop, value)

                    def battery_state(status, current, capacity):
                        result = execute('cat /sys/class/power_supply/axp20x-battery/'
                                         '{status,current_now,capacity}').strip().splitlines()
                        assert result == [status, str(current), str(capacity)], result

                    battery_value('battery-capacity', 50)
                    battery_value('battery-current-ma', 500)
                    battery_state('Charging', 500000, 50)
                    battery_value('battery-capacity', 100)
                    battery_state('Full', 0, 100)
                    battery_value('battery-capacity', 25)
                    battery_value('battery-current-ma', -250)
                    battery_state('Discharging', -250000, 25)
                    battery_value('battery-current-ma', 0)
                    battery_state('Not charging', 0, 25)
                    for capacity in (15, 14, 5, 4, 0, 25):
                        battery_value('battery-capacity', capacity)
                        battery_state('Not charging', 0, capacity)
            # Open evdev before injecting the key. Wait for actual key-down
            # consumption before release, not an assumed timing delay.
            marker = 'UC_PEK_' + uuid.uuid4().hex
            runtime.control('qom-set', {'path': pmic, 'property': 'power-key-pressed', 'value': False})
            reader = '''import os, select, struct, time
from pathlib import Path
event = next(p for p in Path('/sys/class/input').glob('event*')
             if (p/'device/name').read_text().strip() == 'axp20x-pek')
fd = os.open('/dev/input/' + event.name, os.O_RDONLY | os.O_NONBLOCK)
record = struct.Struct('llHHi')
print(MARKER + '_READY', flush=True)
values = []
deadline = time.monotonic() + 15
while len(values) < 2:
    remaining = deadline - time.monotonic()
    assert remaining > 0 and select.select([fd], [], [], remaining)[0], 'No power-key event'
    packet = os.read(fd, record.size)
    _, _, kind, code, value = record.unpack(packet)
    if kind == 1 and code == 116:
        values.append(value)
        if value == 1:
            print(MARKER + '_DOWN', flush=True)
os.close(fd)
assert values == [1, 0], values
print(MARKER + '_PASS', flush=True)
'''.replace('MARKER', repr(marker))
            with ThreadPoolExecutor(max_workers=1) as pool:
                with (runtime.workspace / 'serial.log').open() as serial:
                    serial.seek(0, 2)
                    future = pool.submit(runtime.execute, 'python3 -c ' + shlex.quote(reader), timeout=25)
                    observed = ''

                    def observed_marker(suffix):
                        nonlocal observed
                        deadline = time.monotonic() + 15
                        while marker + suffix not in observed:
                            observed += serial.read()
                            if marker + suffix in observed:
                                return
                            if future.done():
                                raise AssertionError(future.result())
                            if time.monotonic() >= deadline:
                                raise TimeoutError('Guest power-key observer did not report ' + suffix)
                            time.sleep(0.05)

                    observed_marker('_READY')
                    runtime.control('qom-set', {'path': pmic, 'property': 'power-key-pressed', 'value': True})
                    record('power-key-pressed', True)
                    observed_marker('_DOWN')
                    runtime.control('qom-set', {'path': pmic, 'property': 'power-key-pressed', 'value': False})
                    record('power-key-pressed', False)
                    result = future.result(timeout=25)
                    record('guest-key-events', result)
                    assert result['exit_code'] == 0 and marker + '_PASS' in result['stdout'], result
            runtime.stop()
            record('validation', 'passed')
            print(f'PASS: guest PMIC temperature, AC interrupts, battery states, KEY_POWER down/up, clean stop; {evidence}', flush=True)
        except BaseException as exc:
            record('failure', str(exc))
            raise
        finally:
            if runtime.process.poll() is None:
                try:
                    runtime.stop()
                except Exception:
                    runtime.stop(force=True)
                    record('cleanup', 'forced power removal; not clean-shutdown evidence')
            else:
                runtime.release()


if __name__ == '__main__':
    main()
