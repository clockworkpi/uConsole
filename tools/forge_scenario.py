"""Versioned, strictly validated initial device state for an owned paused VM."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path


PROPERTIES = {
    'ac_present': ('ac-present', bool, None),
    'battery_present': ('battery-present', bool, None),
    'battery_voltage_uv': ('battery-voltage-uv', int, (0, 4504500)),
    'battery_capacity': ('battery-capacity', int, (0, 100)),
    'battery_current_ma': ('battery-current-ma', int, (-4095, 4095)),
    'pmic_temperature_mc': ('pmic-temperature-mc', int, (-267700, 141800)),
    'pmic_over_temperature': ('pmic-over-temperature', bool, None),
    'power_key_pressed': ('power-key-pressed', bool, None),
    'adc_input_uv': ('input-uv', int, (0, 3300000)),
    'adc_powered': ('powered', bool, None),
}
ADC_FIELDS = {'adc_input_uv', 'adc_powered'}


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError('Duplicate scenario field: ' + name)
        result[name] = value
    return result


def validate_value(name, value):
    if name not in PROPERTIES:
        raise ValueError('Unknown power field: ' + name)
    _, kind, bounds = PROPERTIES[name]
    if type(value) is not kind or (bounds and not bounds[0] <= value <= bounds[1]):
        raise ValueError('Invalid power scenario value: ' + name)


def power_device(control):
    children = control('qom-list', {'path': '/machine/unattached'})
    devices = [item for item in children if item['type'] == 'child<axp221_pmu>']
    if len(devices) != 1:
        raise ValueError('Scenario requires exactly one AXP221 model')
    path = '/machine/unattached/' + devices[0]['name']
    properties = {item['name']: item['type'] for item in control('qom-list', {'path': path})}
    for name, (prop, kind, _) in PROPERTIES.items():
        if name in ADC_FIELDS:
            continue
        if properties.get(prop) != ('bool' if kind is bool else 'int'):
            raise ValueError('QEMU lacks compatible scenario property: ' + prop)
    return path


def power_paths(control):
    """Validate both model identities and all properties before any write."""
    pmic = power_device(control)
    children = control('qom-list', {'path': '/machine'})
    if not any(item['name'] == 'battery-adc' and item['type'] == 'child<adc101c>'
               for item in children):
        raise ValueError('QEMU lacks the ADC101C model; rebuild the emulator')
    adc = '/machine/battery-adc'
    properties = {item['name']: item['type'] for item in control('qom-list', {'path': adc})}
    for name in ADC_FIELDS:
        prop, kind, _ = PROPERTIES[name]
        if properties.get(prop) != ('bool' if kind is bool else 'int'):
            raise ValueError('QEMU lacks compatible ADC scenario property: ' + prop)
    return {name: adc if name in ADC_FIELDS else pmic for name in PROPERTIES}


def read_power(control, paths):
    values = {}
    for name, (prop, _, _) in PROPERTIES.items():
        value = control('qom-get', {'path': paths[name], 'property': prop})
        validate_value(name, value)
        values[name] = value
    return values


def query_power(control):
    """Read sampled values; a running guest may change state between reads."""
    return {'power': read_power(control, power_paths(control))}


def change_power(control, name, value, *, record=None):
    """One model-validated update, with full before/after samples; no rollback."""
    validate_value(name, value)
    paths = power_paths(control)
    before = read_power(control, paths)
    arguments = {'path': paths[name], 'property': PROPERTIES[name][0], 'value': value}
    if record:
        record({'before': before})
        record({'state': 'dispatch', 'command': 'qom-set', 'arguments': arguments})
    control('qom-set', arguments)
    if record:
        record({'state': 'acknowledged', 'command': 'qom-set'})
    try:
        after = read_power(control, paths)
    except Exception as exc:
        raise ValueError('Power write acknowledged, but readback failed; effect may have occurred '
                         'and is not rolled back: ' + str(exc)) from exc
    expected = value // 1100 * 1100 if name == 'battery_voltage_uv' else value
    if name == 'pmic_temperature_mc':
        expected = value // 100 * 100
    if after[name] != expected:
        raise ValueError('Power readback mismatch; mutation may have occurred and is not rolled back')
    return {'requested': {name: value}, 'before': before, 'observed': after,
            'coverage': 'Sampled state, not an atomic guest snapshot or physical timing proof'}


@dataclass(frozen=True)
class Power:
    ac_present: bool = True
    battery_present: bool = False
    battery_voltage_uv: int = 0
    battery_capacity: int = 100
    battery_current_ma: int = 0
    pmic_temperature_mc: int = 25000
    pmic_over_temperature: bool = False
    power_key_pressed: bool = False
    adc_input_uv: int = 0
    adc_powered: bool = True


@dataclass(frozen=True)
class Scenario:
    sha256: str
    power: Power

    @classmethod
    def load(cls, path):
        with Path(path).open('rb') as source:
            payload = source.read(16385)
        if len(payload) > 16384:
            raise ValueError('Scenario exceeds 16 KiB')
        data = json.loads(payload, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {'schema', 'power'}:
            raise ValueError('Scenario requires exactly schema and power fields')
        if type(data['schema']) is not int or data['schema'] != 1:
            raise ValueError('Unsupported scenario schema; expected integer 1')
        values = data['power']
        if not isinstance(values, dict) or not set(values) <= PROPERTIES.keys():
            raise ValueError('Unknown or invalid power scenario fields')
        for name, value in values.items():
            validate_value(name, value)
        power = Power(**values)
        if power.battery_current_ma and not power.battery_present:
            raise ValueError('Nonzero current requires a present battery')
        if power.battery_current_ma > 0 and (not power.ac_present or power.battery_capacity == 100):
            raise ValueError('Charging requires AC and capacity below 100')
        return cls(hashlib.sha256(payload).hexdigest(), power)

    def apply(self, control):
        """Apply before first CPU execution; never accept an unverified no-op."""
        if control('query-status')['running']:
            raise ValueError('Initial scenario requires a paused VM')
        paths = power_paths(control)
        desired = asdict(self.power)
        # Clear current first so removing power/battery cannot conflict with
        # state left by a previously applied initial profile.
        control('qom-set', {'path': paths['battery_current_ma'], 'property': 'battery-current-ma', 'value': 0})
        for name, (prop, _, _) in PROPERTIES.items():
            control('qom-set', {'path': paths[name], 'property': prop, 'value': desired[name]})
        actual = {}
        for name, (prop, _, _) in PROPERTIES.items():
            actual[name] = control('qom-get', {'path': paths[name], 'property': prop})
            expected = desired[name]
            if name == 'battery_voltage_uv':
                expected = expected // 1100 * 1100
            if name == 'pmic_temperature_mc':
                expected = expected // 100 * 100
            if type(actual[name]) is not type(expected) or actual[name] != expected:
                raise ValueError('Scenario readback mismatch: ' + prop)
        return {'schema': 1, 'source_sha256': self.sha256,
                'requested': {'power': desired}, 'observed': {'power': actual},
                'coverage': 'Sampled power state; no physical timing or autonomous battery chemistry'}
