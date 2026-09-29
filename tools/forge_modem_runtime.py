"""Modem worker owned by one Runtime and its private control namespace."""
import json
import socket
import threading
import time

from forge_modem_at import ModemState
from serve_modem_at import serve_channels


class OwnedModem:
    def __init__(self, runtime):
        self.runtime = runtime
        self.connections = {}
        self.owner = self.adapter = self.worker = None
        self.ready = threading.Event()
        self.failure = None
        self.sequence = 0
        self.mutex = threading.Lock()
        self.vm_dead = False

    def start(self):
        from uconsole_emulator import control_connection
        if self.worker is not None:
            raise ValueError('Modem worker already started')
        try:
            for name in ('primary', 'secondary'):
                self.runtime.require_alive()
                self.connections[name] = control_connection(
                    self.runtime.qmp_endpoint.parent / ('modem-' + name))
            self.owner, self.adapter = socket.socketpair()
            self.owner.settimeout(10)
            def link(up):
                if not self.vm_dead:
                    self.runtime.control('set_link', {'name': 'modem-device', 'up': up})
                self.ready.set()
            def serve():
                try:
                    serve_channels(self.connections, ModemState(), link, self.adapter)
                except Exception as exc:
                    self.failure = str(exc)
                finally:
                    # Wake a waiting owner immediately on worker failure. A
                    # still-open adapter socket must not look like a live peer.
                    try:
                        self.adapter.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.ready.set()
            self.worker = threading.Thread(target=serve, name='forge-modem', daemon=True)
            self.worker.start()
            if not self.ready.wait(10):
                raise TimeoutError('Modem link initialization timed out')
            self.require_alive()
        except BaseException:
            self.stop(vm_dead=self.runtime.process.poll() is not None)
            raise
        return self

    def require_alive(self):
        self.runtime.require_alive()
        if self.failure or self.worker is None or not self.worker.is_alive():
            raise RuntimeError('Modem worker unavailable: ' + str(self.failure))

    def change(self, changes):
        return self._request({'changes': changes}, 'applied')

    def query(self):
        return self._request({'query': True}, 'observed')

    def connect(self, connected):
        """Change the USB cable state, retaining SIM/PDP and worker ownership."""
        if type(connected) is not bool:
            raise ValueError('Modem connected state must be boolean')
        with self.mutex:
            self.require_alive()
            children = self.runtime.control('qom-list', {'path':'/machine/peripheral'})
            matches = [item for item in children if item.get('name') == 'modem-device']
            if len(matches) != 1 or matches[0].get('type') != 'child<usb-forge-modem>':
                raise ValueError('Owned modem QOM identity mismatch')
            arguments = {'path':'/machine/peripheral/modem-device', 'property':'attached'}
            before = self.runtime.control('qom-get', arguments)
            if type(before) is not bool:
                raise ValueError('Invalid modem USB attachment state')
            if before != connected:
                try:
                    self.runtime.control('qom-set', dict(arguments, value=connected))
                    observed = self.runtime.control('qom-get', arguments)
                    if type(observed) is not bool or observed != connected:
                        raise RuntimeError('Modem attachment readback mismatch')
                except Exception:
                    self.failure = 'Uncertain USB attachment result; inspect before restart'
                    raise
            return {'before':before, 'connected':connected,
                    'coverage':'USB attachment only; guest enumeration must be checked separately'}

    def inspect(self):
        """Sample worker health without sending commands or claiming link state."""
        alive = self.worker is not None and self.worker.is_alive()
        return {'worker_alive': alive, 'status': 'available' if alive and not self.failure else 'unavailable',
                'error': self.failure, 'sequence': self.sequence,
                'coverage': 'Worker health only; not an observed modem/link snapshot'}

    def _request(self, payload, status):
        with self.mutex:
            self.require_alive()
            self.sequence += 1
            request = (json.dumps(dict(sequence=self.sequence, **payload))+'\n').encode()
            if len(request) > 4097:
                raise ValueError('Modem scenario request too large')
            try:
                deadline = time.monotonic() + 10
                self.owner.settimeout(10)
                self.owner.sendall(request)
                response = bytearray()
                while not response.endswith(b'\n'):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('Modem acknowledgement deadline')
                    self.owner.settimeout(remaining)
                    chunk = self.owner.recv(1)
                    if not chunk or len(response) >= 4096:
                        raise RuntimeError('Missing or oversized modem acknowledgement')
                    response.extend(chunk)
                result = json.loads(response)
                if result.get('sequence') != self.sequence or result.get('status') != status:
                    raise RuntimeError('Modem acknowledgement identity mismatch')
                return result
            except Exception:
                self.failure = self.failure or 'Uncertain scenario result; no automatic retry'
                raise

    def stop(self, *, vm_dead=False):
        with self.mutex:
            self.vm_dead = vm_dead
            for conn in [self.adapter, *self.connections.values()]:
                if conn is not None:
                    try:
                        conn.shutdown(socket.SHUT_RD)
                    except OSError:
                        pass
            if self.worker is not None:
                self.worker.join(15)
                if self.worker.is_alive():
                    raise RuntimeError('Modem worker did not stop; ownership retained')
            for conn in [self.owner, self.adapter, *self.connections.values()]:
                if conn is not None:
                    conn.close()
            self.connections.clear()
            self.owner = self.adapter = None
            if self.failure and not vm_dead:
                raise RuntimeError('Modem shutdown is uncertain: ' + self.failure)
