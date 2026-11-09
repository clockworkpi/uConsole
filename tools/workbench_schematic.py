"""Code-linked functional diagram data; no device writes or Tk dependencies.

State observations and activity are deliberately separate. A successful query
must never make every device glow as if it had performed a bus transaction.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class Source:
    label: str
    path: str
    symbol: str
    anchor: bool = False

    def resolve(self, root):
        """Resolve against installed or checkout source, not saved line numbers."""
        root = Path(root).resolve()
        path = (root / self.path).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Source link escapes the source tree')
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('Source file is too large for diagram navigation')
        text = path.read_text(encoding='utf-8')
        if self.anchor:
            matches = [n for n, line in enumerate(text.splitlines(), 1) if self.symbol in line]
        elif path.suffix == '.py':
            matches = [node.lineno for node in ast.walk(ast.parse(text))
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                       and node.name == self.symbol]
        else:
            lines = text.splitlines()
            if path.suffix == '.patch':
                lines = [line[1:] if line.startswith(('+', ' ')) else '' for line in lines]
            matches = [n for n, line in enumerate(lines, 1)
                       if re.match(r'^\s*(?:void|int|bool|static\s+void)\s+'
                                   + re.escape(self.symbol) + r'\s*\(', line)]
        if len(matches) != 1:
            raise ValueError(f'Source symbol is missing or ambiguous: {self.symbol}')
        return path, matches[0], text


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    position: tuple
    hardware: str
    reference: str
    fidelity: str
    sources: tuple


COMPONENTS = (
    Component('core', 'CM4 / SoC', (400, 240), 'CM4 adapter and compute module',
              'clockwork_Adapter_CM4_Schematic.pdf', 'Partial Raspberry Pi 4 machine model',
              (Source('Machine configuration', 'tools/uconsole_emulator.py', 'command'),
               Source('Owned runtime', 'tools/forge_runtime.py', 'Runtime'))),
    Component('power', 'PMIC / battery', (90, 70), 'U101 AXP228 (Linux/QEMU AXP221 identity) and battery sensing',
              'clockwork_Mainboard_V3.14_Schematic.pdf', 'AXP221 / ADC101C virtual models',
              (Source('Power readback', 'tools/forge_scenario.py', 'query_power'),
               Source('Power changes', 'tools/forge_scenario.py', 'change_power'),
               Source('QEMU PMIC IRQ model patch', 'Code/patch/qemu/axp2xx-poweroff.patch', 'axp221_update_irq'),
               Source('QEMU battery ADC model', 'Code/patch/qemu/adc101c.c', 'adc101c_update'))),
    Component('keyboard', 'Keyboard / trackball', (90, 240), 'Keyboard MCU and pointing input',
              'keyboard_220816.pdf', 'Firmware report bridge / USB surrogate',
              (Source('Keyboard bridge', 'tools/forge_keyboard.py', 'KeyboardBridge'),
               Source('HID reports', 'tools/keyboard_reports.py', 'Reports'),
               Source('QEMU HID transport model', 'Code/patch/qemu/uconsole-keyboard.patch', 'uconsole_data'),
               Source('Device firmware', 'Code/uconsole_keyboard/uconsole_keyboard.ino', 'loop'))),
    Component('display', 'Display', (710, 70), 'Display interface',
              'clockwork_Mainboard_V3.14_Schematic.pdf', 'Mailbox framebuffer surrogate; not native DSI / GPU',
              (Source('Display setup', 'tools/uconsole_emulator.py', 'configure_display'),
               Source('QEMU redraw instrumentation patch', 'Code/patch/qemu/forge-observation-counters.patch',
                      'object_property_add_uint64_ptr(obj, "display-updates"', anchor=True))),
    Component('storage', 'Storage', (710, 240), 'SD / boot storage',
              'clockwork_Adapter_CM4_Schematic.pdf', 'Emulated SD storage backed by guest image',
              (Source('Image preparation', 'tools/uconsole_emulator.py', 'prepare'),
               Source('Machine drives', 'tools/uconsole_emulator.py', 'command'),
               Source('QEMU SD instrumentation patch', 'Code/patch/qemu/forge-observation-counters.patch',
                      'sd->observed_write_bytes += len;', anchor=True))),
    Component('audio', 'Audio', (90, 410), 'Mainboard audio output',
              'clockwork_Mainboard_V3.14_Schematic.pdf', 'USB audio surrogate; not native codec',
              (Source('Audio readback', 'tools/forge_audio.py', 'query'),
               Source('QEMU synthetic capture model', 'Code/patch/qemu/forge-audio-capture.patch', 'data'))),
    Component('modem', '4G modem', (710, 410), 'Optional 4G expansion',
              'clockwork_UC_4G_Schematic.pdf', 'Synthetic USB modem; no radio network',
              (Source('Owned modem', 'tools/forge_modem_runtime.py', 'OwnedModem'),
               Source('QEMU modem USB model', 'Code/patch/qemu/forge-modem.c', 'transfer'))),
)
BY_ID = {component.id: component for component in COMPONENTS}
# Exact printed tokens, qualified by each component's named schematic sheet.
# These map to functional implementations, not to a claim of net-level fidelity.
REFERENCE_TOKENS = {
    'core': ('J2', 'J3'),
    'power': ('U101', 'AXP228'),
    'keyboard': ('U1', 'GD32F103Rx'),
    'display': ('LCD_RESET', 'DSI1_CP', 'DSI1_DP0'),
    'storage': ('SDX_CLK', 'SDX_CMD', 'SDX_D0'),
    'audio': ('U403', 'U404', 'AUDIO_L', 'AUDIO_R'),
    'modem': ('U1', 'SIM7600G-H'),
}
# Functional relationships, not an assertion of net-level electrical fidelity.
LINKS = (('power', 'core', 'power / I²C'), ('keyboard', 'core', 'USB HID'),
         ('core', 'display', 'framebuffer surrogate'), ('core', 'storage', 'SD'),
         ('core', 'audio', 'USB surrogate'), ('core', 'modem', 'USB surrogate'))
STATES = frozenset(('unknown', 'present', 'configured', 'active', 'fault', 'disconnected'))


class Observations:
    """Bounded, identity-scoped latest observations with explicit stale state."""
    def __init__(self, stale_after=3.0, pulse_for=0.6):
        self.stale_after, self.pulse_for = stale_after, pulse_for
        self.identity = None
        self.values = {}
        self.dropped = 0

    def bind(self, identity):
        if identity != self.identity:
            self.identity = identity
            self.values.clear()
            self.dropped = 0

    def accept(self, event):
        if event['identity'] != self.identity or self.identity is None:
            return False
        component = event['component']
        if component not in BY_ID or event['state'] not in STATES:
            raise ValueError('Unknown diagram component or state')
        stamp = event['time']
        if type(stamp) not in (int, float) or not 0 <= stamp < float('inf'):
            raise ValueError('Invalid observation timestamp')
        previous = self.values.get(component)
        if previous and stamp < previous['time']:
            self.dropped += 1
            return False
        if not isinstance(event['source'], str) or not isinstance(event['detail'], str):
            raise ValueError('Observation source and detail must be text')
        if len(event['source']) > 256 or len(event['detail']) > 4096:
            raise ValueError('Observation text exceeds bounds')
        self.values[component] = {key: event[key] for key in
                                  ('identity', 'component', 'state', 'time', 'source', 'detail')}
        return True

    def view(self, component, now):
        event = self.values.get(component)
        if event is None:
            return dict(state='unknown', detail='Not instrumented / no observation', source='', time=None)
        result = dict(event)
        age = now - event['time']
        if age < 0 or age > self.stale_after:
            result['state'] = 'stale'
        elif event['state'] == 'active' and age > self.pulse_for:
            result['state'] = 'present'
            result['detail'] = 'Last activity: ' + event['detail']
        return result
