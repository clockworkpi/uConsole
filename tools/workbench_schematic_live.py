"""Bounded background observations from an explicitly owned Runtime.

No discovery by port or PID, no guest shell commands, no device writes. Only
counter deltas or observed state changes are activity, never query frequency.
"""
import json
import threading
import time

from forge_scenario import query_power


class Sampler:
    def __init__(self):
        self.reset()

    def reset(self):
        self.identity = None
        self.counters = {}

    def sample(self, runtime):
        if runtime.identity != self.identity:
            self.identity = runtime.identity
            self.counters.clear()
        events = []

        def event(component, state, source, detail):
            events.append(dict(identity=runtime.identity, component=component, state=state,
                               source=source, detail=str(detail)[:4096], time=time.monotonic()))

        def read(component, callback):
            try:
                callback()
            except BlockingIOError:
                # A control job has priority. Absence of a fresh sample goes
                # stale in the view, rather than inventing a disconnected state.
                pass
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                event(component, 'unknown', 'Observation unavailable', str(exc))

        def core():
            status = runtime.observe('query-status')
            event('core', 'present', 'QMP query-status', status['status'])

        def storage():
            devices = runtime.observe('qom-list', {'path': '/machine/unattached'})
            cards = [device for device in devices if device.get('type') == 'child<sd-card>']
            if len(cards) != 1:
                raise ValueError('Expected one owned SD card for storage observations')
            path = '/machine/unattached/' + cards[0]['name']
            properties = runtime.observe('qom-list', {'path': path})
            fields = ('observed-read-bytes', 'observed-write-bytes')
            if not all(any(p.get('name') == field and p.get('type') == 'uint64' for p in properties) for field in fields):
                event('storage', 'present', 'QOM SD inventory', 'SD activity counters unavailable; rebuild patched QEMU')
                return
            totals = [runtime.observe('qom-get', {'path': path, 'property': field}) for field in fields]
            if any(type(value) is not int or not 0 <= value <= 2**64 - 1 for value in totals):
                raise ValueError('Invalid SD observation counter')
            before = self.counters.get('storage')
            self.counters['storage'] = totals
            delta = [totals[i] - before[i] for i in range(2)] if before else [0, 0]
            changed = before is not None and all(n >= 0 for n in delta) and any(delta)
            event('storage', 'active' if changed else 'present', 'QOM SD read/write counters',
                  f'Successful SD read/write bytes: {totals}; interval delta: {delta}')

        def power():
            values = query_power(runtime.observe)['power']
            before = self.counters.get('power')
            self.counters['power'] = values
            changed = before is not None and before != values
            event('power', 'active' if changed else 'present', 'QOM power readback',
                  ('Observed state change; not I²C traffic. ' if changed else 'Sampled state; not I²C traffic. ')
                  + json.dumps(values, sort_keys=True))

        def counter(component, path, prop, meaning):
            properties = runtime.observe('qom-list', {'path': path})
            if not any(item.get('name') == prop and item.get('type') == 'uint64' for item in properties):
                event(component, 'present', 'QOM inventory',
                      'Activity counter unavailable; rebuild patched QEMU for observation support')
                return
            value = runtime.observe('qom-get', {'path': path, 'property': prop})
            if type(value) is not int or not 0 <= value <= 2**64 - 1:
                raise ValueError('Invalid device observation counter')
            key = (component, prop)
            previous = self.counters.get(key)
            self.counters[key] = value
            delta = value - previous if previous is not None else 0
            event(component, 'active' if delta > 0 else 'present', 'QOM ' + prop,
                  f'{meaning}: {value}; interval delta: {delta}; '
                  + ('baseline reset' if delta < 0 else 'host observation, not electrical timing'))

        def display():
            parent = '/machine/soc/peripherals'
            children = runtime.observe('qom-list', {'path': parent})
            matches = [child for child in children if child['name'] == 'fb']
            if len(matches) != 1 or matches[0]['type'] != 'child<bcm2835-fb>':
                raise ValueError('Framebuffer model is not present at the expected path')
            counter('display', parent + '/fb', 'display-updates',
                    'Framebuffer redraw notifications (may include host invalidation; not native DSI frames)')

        def usb(component, device_id, expected):
            children = runtime.observe('qom-list', {'path': '/machine/peripheral'})
            matches = [child for child in children if child['name'] == device_id]
            if not matches:
                event(component, 'disconnected', 'QOM inventory', 'Virtual device is absent')
                return
            if len(matches) != 1 or matches[0]['type'] not in expected:
                raise ValueError('Unexpected virtual device identity')
            attached = runtime.observe('qom-get', {'path': '/machine/peripheral/' + device_id,
                                                  'property': 'attached'})
            if type(attached) is not bool:
                raise ValueError('Invalid USB attachment value')
            event(component, 'present' if attached else 'disconnected', 'QOM USB attachment',
                  'Attached; traffic not instrumented' if attached else 'USB detached')
            if attached and component == 'keyboard':
                counter('keyboard', '/machine/peripheral/' + device_id, 'delivered-reports',
                        'Successful virtual USB HID IN deliveries')

        read('core', core)
        read('storage', storage)
        read('power', power)
        read('keyboard', lambda: usb('keyboard', 'deck', {'child<usb-uconsole-keyboard>'}))
        read('audio', lambda: usb('audio', 'audio-surrogate', {'child<usb-audio>', 'child<usb-forge-capture>'}))
        read('modem', lambda: usb('modem', 'modem-device', {'child<usb-forge-modem>'}))
        read('display', display)
        return events


class Collector:
    """At most one daemon worker and one pending batch, independent of Tk."""
    def __init__(self, interval=1.0, sampler=None):
        self.interval = interval
        self.sampler = sampler or Sampler()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.runtime = None
        self.pending = None
        self.dropped = 0
        self.generation = 0
        self.worker = threading.Thread(target=self.run, name='schematic-observer', daemon=True)
        self.worker.start()

    def bind(self, runtime):
        with self.lock:
            if runtime is not self.runtime:
                self.generation += 1
                self.runtime = runtime
                self.pending = None
                self.dropped = 0

    def take(self):
        with self.lock:
            result, self.pending = self.pending, None
            return result

    def close(self):
        self.stopped.set()
        self.bind(None)

    def run(self):
        previous_generation = None
        while not self.stopped.wait(self.interval):
            with self.lock:
                runtime = self.runtime
                generation = self.generation
            if runtime is None:
                continue
            if generation != previous_generation:
                reset = getattr(self.sampler, 'reset', None)
                if reset is not None:
                    reset()
                previous_generation = generation
            try:
                batch = self.sampler.sample(runtime)
            except Exception as exc:
                batch = [dict(identity=runtime.identity, component='core', state='fault',
                              source='Observer failure', detail=str(exc)[:4096], time=time.monotonic())]
            with self.lock:
                if runtime is self.runtime and generation == self.generation and not self.stopped.is_set():
                    if self.pending is not None:
                        self.dropped += len(self.pending)
                    self.pending = batch
