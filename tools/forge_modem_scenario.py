"""Bounded scenario protocol for an owner-supplied private socketpair.

This is not a public listener. Sequence numbers are increasing per attachment;
an uncertain caller must inspect/restart, never replay a mutation blindly.
"""
import json

from forge_scenario import unique_object


class ScenarioChannel:
    def __init__(self, modem):
        self.modem = modem
        self.pending = bytearray()
        self.sequence = 0

    def finish(self):
        if self.pending:
            raise ValueError('Truncated modem scenario request at EOF')

    def feed(self, data):
        output = {}
        def append(name, payload):
            output.setdefault(name, bytearray()).extend(payload)
        for byte in data:
            if byte != 10:
                if len(self.pending) >= 4096:
                    raise BufferError('Modem scenario frame exceeds 4096 bytes')
                self.pending.append(byte)
                continue
            raw = bytes(self.pending)
            self.pending.clear()
            request = json.loads(raw, object_pairs_hook=unique_object)
            query = isinstance(request, dict) and set(request) == {'sequence', 'query'} and request['query'] is True
            change = isinstance(request, dict) and set(request) == {'sequence', 'changes'} and isinstance(request['changes'], dict)
            if (not (query or change) or
                    type(request['sequence']) is not int or
                    not self.sequence < request['sequence'] <= 2**53 - 1):
                raise ValueError('Invalid or replayed modem scenario request')
            # Consume sequence before dispatch. Failure never authorizes replay.
            self.sequence = request['sequence']
            if change:
                for name, payload in self.modem.set_scenario(**request['changes']).items():
                    append(name, payload)
            elif self.modem.link_failed:
                raise RuntimeError('Modem link state is uncertain; observed state unavailable')
            state = self.modem.state
            response = dict(sequence=self.sequence, status='observed' if query else 'applied',
                            observed=dict(sim=state.sim, radio=state.radio,
                                          registration=state.registered(),
                                          rssi=state.rssi, ber=state.ber,
                                          link_up=self.modem.link_up))
            append('scenario', (json.dumps(response, sort_keys=True)+'\n').encode())
        return {name: bytes(payload) for name, payload in output.items()}
