"""Deterministic SIM7600 AT subset for Forge's modem application contract.

No RF, real SIM access, host commands, or physical firmware updates.
Synthetic packet-session state is shared by the modem AT ports;
each port owns its own bounded command framing and echo/error settings.
"""
from dataclasses import dataclass, field, fields, replace
import re
from forge_modem_pdp import PacketData


@dataclass
class ModemState:
    sim: str = 'ready'
    pin: str = '1234'  # Disposable simulated SIM only.
    pin_attempts: int = 3
    radio: int = 1
    registration: int = 1
    rssi: int = 20
    ber: int = 99
    packet: PacketData = field(default_factory=PacketData)

    def __post_init__(self):
        if (self.sim not in ('ready','absent','pin','puk') or not isinstance(self.pin, str)
                or not re.fullmatch('[0-9]{4,8}', self.pin)):
            raise ValueError('Invalid synthetic SIM state')
        for value, allowed in ((self.pin_attempts, range(4)), (self.radio, (0,1,4)),
                               (self.registration, range(6)), (self.rssi, (*range(32),99)),
                               (self.ber, (*range(8),99))):
            if type(value) is not int or value not in allowed:
                raise ValueError('Invalid modem scenario value')
        if self.sim == 'pin' and self.pin_attempts == 0:
            raise ValueError('Exhausted PIN attempts require PUK state')

    def registered(self):
        return self.registration if self.sim == 'ready' and self.radio == 1 else 0

    def network_ready(self):
        return self.registered() in (1, 5) and self.packet.attached and bool(self.packet.active)


ERRORS = {3: 'operation not allowed', 10: 'SIM not inserted', 11: 'SIM PIN required',
          12: 'SIM PUK required', 16: 'incorrect password'}


@dataclass
class ATPort:
    state: ModemState = field(default_factory=ModemState)
    echo: bool = True
    errors: int = 0
    reports: dict = field(default_factory=lambda: {'CREG': 0, 'CGREG': 0, 'CEREG': 0})
    pending: bytearray = field(default_factory=bytearray)
    overflow: bool = False
    limit: int = 1024

    def error(self, code):
        if self.errors == 0:
            return 'ERROR'
        return '+CME ERROR: ' + (str(code) if self.errors == 1 else ERRORS[code])

    def execute(self, command):
        upper = command.upper()
        packet = self.state.packet.execute(command, self.state.registered() in (1, 5), self.error)
        if packet is not None:
            return packet
        if upper == 'AT':
            return ['OK']
        if upper in ('ATE0','ATE1'):
            self.echo = upper[-1] == '1'
            return ['OK']
        if upper == 'AT+CMEE?':
            return ['+CMEE: ' + str(self.errors), 'OK']
        if re.fullmatch(r'AT\+CMEE=[012]', upper):
            self.errors = int(upper[-1])
            return ['OK']
        if upper == 'AT+CPIN?':
            if self.state.sim == 'absent':
                return [self.error(10)]
            return ['+CPIN: ' + {'ready':'READY','pin':'SIM PIN','puk':'SIM PUK'}[self.state.sim], 'OK']
        match = re.fullmatch(r'AT\+CPIN="([0-9]{4,8})"', upper)
        if match:
            if self.state.sim != 'pin':
                return [self.error({'absent':10,'ready':3,'puk':12}[self.state.sim])]
            if match[1] == self.state.pin:
                self.state.sim = 'ready'
                self.state.pin_attempts = 3
                return ['OK']
            self.state.pin_attempts -= 1
            if self.state.pin_attempts == 0:
                self.state.sim = 'puk'
            return [self.error(16)]
        if upper == 'AT+CFUN?':
            return ['+CFUN: ' + str(self.state.radio), 'OK']
        if re.fullmatch(r'AT\+CFUN=[014]', upper):
            self.state.radio = int(upper[-1])
            return ['OK']
        if upper == 'AT+CSQ':
            values = (self.state.rssi,self.state.ber) if self.state.radio == 1 else (99,99)
            return ['+CSQ: %d,%d' % values, 'OK']
        match = re.fullmatch(r'AT\+(CREG|CGREG|CEREG)(\?|=[01])', upper)
        if match:
            name, suffix = match.groups()
            if suffix == '?':
                return ['+%s: %d,%d' % (name,self.reports[name],self.state.registered()), 'OK']
            self.reports[name] = int(suffix[-1])
            return ['OK']
        return ['ERROR']

    def notifications(self):
        """Caller fans state-change notifications out to each subscribed port."""
        return ''.join('\r\n+%s: %d\r\n' % (name,self.state.registered())
                       for name, mode in self.reports.items() if mode).encode('ascii')

    def feed(self, data):
        """Consume arbitrary serial chunks; an oversized line is discarded whole."""
        output = bytearray()
        for byte in data:
            if byte == 10:
                # Ignore LF only between commands (the LF in CRLF). Never
                # splice separate lines into a valid state-changing command.
                if self.pending:
                    self.pending.clear()
                    self.overflow = True
                continue
            if byte == 13:
                if self.overflow:
                    output.extend(b'\r\nERROR\r\n')
                elif self.pending:
                    raw = bytes(self.pending)
                    if self.echo:
                        output.extend(raw + b'\r')
                    try:
                        reply = self.execute(raw.decode('ascii'))
                    except UnicodeDecodeError:
                        reply = ['ERROR']
                    output.extend(('\r\n'+'\r\n'.join(reply)+'\r\n').encode('ascii'))
                self.pending.clear()
                self.overflow = False
            elif not self.overflow:
                if len(self.pending) >= self.limit:
                    self.pending.clear()
                    self.overflow = True
                else:
                    self.pending.append(byte)
        return bytes(output)


class Modem:
    """One shared modem, two AT channels, deterministic ordered state changes.

    Callers serialize calls on their event loop and deliver returned bytes to
    the named channels. No background threads, wall clock, or unbounded queues.
    """
    def __init__(self, state=None, link_changed=None):
        self.state = state if state is not None else ModemState()
        self.ports = {name: ATPort(self.state) for name in ('primary', 'secondary')}
        self.link_changed = link_changed
        self.link_failed = False
        self.link_up = None
        self._sync_link()

    def _sync_link(self):
        if self.link_failed:
            raise RuntimeError('Modem network state is uncertain; restart required')
        up = self.state.network_ready()
        if up != self.link_up:
            try:
                if self.link_changed is not None:
                    self.link_changed(up)
            except Exception:
                self.link_failed = True
                raise
            self.link_up = up

    def _changed(self, before, output):
        # Synchronize before returning any successful AT result or URC. An
        # uncertain external update poisons this instance instead of retrying.
        self._sync_link()
        if before != self.state.registered():
            for name, port in self.ports.items():
                output[name].extend(port.notifications())

    @staticmethod
    def _result(output):
        return {name: bytes(value) for name, value in output.items() if value}

    def feed(self, channel, data):
        if self.link_failed:
            raise RuntimeError('Modem network state is uncertain; restart required')
        if channel not in self.ports:
            raise ValueError('Only primary and secondary AT channels accept commands')
        if not isinstance(data, bytes):
            raise TypeError('Serial input must be bytes')
        output = {name: bytearray() for name in self.ports}
        # Observe every terminator, not just the final state of a batch. Turning
        # RF off then on in one chunk must produce both registration changes.
        for byte in data:
            before = self.state.registered()
            output[channel].extend(self.ports[channel].feed(bytes((byte,))))
            self._changed(before, output)
        return self._result(output)

    def set_scenario(self, **changes):
        if self.link_failed:
            raise RuntimeError('Modem network state is uncertain; restart required')
        allowed = {'sim','pin','pin_attempts','radio','registration','rssi','ber'}
        if not changes or not set(changes) <= allowed:
            raise ValueError('Unknown or empty modem scenario change')
        candidate = replace(self.state, **changes)  # Validate before any mutation.
        before = self.state.registered()
        for member in fields(candidate):
            setattr(self.state, member.name, getattr(candidate, member.name))
        output = {name: bytearray() for name in self.ports}
        self._changed(before, output)
        return self._result(output)
