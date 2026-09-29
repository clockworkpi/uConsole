"""Synthetic IPv4 PDP subset; no carrier provisioning or PPP data mode.

Command grammar follows SIM7500/SIM7600 AT manual V3 sections 8.2.2–8.2.4.
The initial context is the prototype's existing automatic IPv4 bearer.
Unsupported address families/options fail instead of claiming configuration.
"""
from dataclasses import dataclass, field
import re


def valid_cid(cid):
    return 1 <= cid <= 24 or 100 <= cid <= 179


@dataclass
class PacketData:
    contexts: dict = field(default_factory=lambda: {1: ''})
    active: set = field(default_factory=lambda: {1})
    attached: bool = True

    def execute(self, command, registered, error):
        upper = command.upper()
        if upper == 'AT+CGDCONT=?':
            return ['+CGDCONT: (1-24,100-179),"IP",,,(0),(0),(0),(0)', 'OK']
        if upper == 'AT+CGDCONT?':
            return ['+CGDCONT: %d,"IP","%s","",0,0,0,0' % (cid, apn)
                    for cid, apn in sorted(self.contexts.items())] + ['OK']
        match = re.fullmatch(r'AT\+CGDCONT=([0-9]{1,3})(?:,"IP","([A-Za-z0-9._-]{0,100})")?',
                             command, flags=re.IGNORECASE)
        if match:
            cid = int(match[1])
            if not valid_cid(cid) or cid in self.active:
                return [error(3)]
            if match[2] is None:
                self.contexts.pop(cid, None)
            else:
                self.contexts[cid] = match[2]
            return ['OK']
        if upper == 'AT+CGACT=?':
            return ['+CGACT: (0,1)', 'OK']
        if upper == 'AT+CGACT?':
            return ['+CGACT: %d,%d' % (cid, int(cid in self.active and registered and self.attached))
                    for cid in sorted(self.contexts)] + ['OK']
        match = re.fullmatch(r'AT\+CGACT=([01])(?:,([0-9]{1,3}))?', upper)
        if match:
            targets = set(self.contexts) if match[2] is None else {int(match[2])}
            if not targets or not targets <= self.contexts.keys():
                return [error(3)]
            if match[1] == '1':
                if not registered or not self.attached:
                    return [error(3)]
                self.active.update(targets)
            else:
                self.active.difference_update(targets)
            return ['OK']
        if upper == 'AT+CGATT=?':
            return ['+CGATT: (0,1)', 'OK']
        if upper == 'AT+CGATT?':
            return ['+CGATT: %d' % int(self.attached and registered), 'OK']
        match = re.fullmatch(r'AT\+CGATT=([01])', upper)
        if match:
            if match[1] == '1' and not registered:
                return [error(3)]
            self.attached = match[1] == '1'
            if not self.attached:
                self.active.clear()
            return ['OK']
        if upper.startswith(('AT+CGDCONT', 'AT+CGACT', 'AT+CGATT')):
            return ['ERROR']
        return None
