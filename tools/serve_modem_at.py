"""Attach the experimental AT engine to QEMU's loopback modem chardev.

Accepts one or two loopback chardev sockets and shares modem state between them.
USB descriptors belong to QEMU, not this adapter. No real serial devices or
remote hosts are accepted by this CLI.
"""
import argparse
import socket
import selectors
import re
import signal
from contextlib import ExitStack

from forge_modem_at import Modem, ModemState
from forge_modem_scenario import ScenarioChannel


def serve(connection, state):
    serve_channels({'primary': connection}, state)


def serve_channels(connections, state, link_changed=None, scenario_connection=None):
    """One event loop and shared modem for the two command channels."""
    if not connections or not set(connections) <= {'primary','secondary'}:
        raise ValueError('Only primary and secondary AT channel sockets are supported')
    active = dict(connections)
    if scenario_connection is not None:
        active['scenario'] = scenario_connection
    all_connections = dict(active)
    pending = {name: bytearray() for name in active}
    eof = set()
    original = {name: conn.gettimeout() for name, conn in active.items()}
    modem = None
    try:
        modem = Modem(state, link_changed=link_changed)
        scenario = ScenarioChannel(modem)
        with selectors.DefaultSelector() as selector:
            for name, conn in active.items():
                conn.setblocking(False)
                selector.register(conn, selectors.EVENT_READ, name)
            while active:
                for key, events in selector.select():
                    name, conn = key.data, key.fileobj
                    if name not in active:
                        continue
                    if events & selectors.EVENT_READ:
                        try:
                            data = conn.recv(4096)
                        except BlockingIOError:
                            data = None
                        if data == b'':
                            if name == 'scenario':
                                scenario.finish()
                            eof.add(name)
                            selector.modify(conn, selectors.EVENT_WRITE, name)
                        if data:
                            outputs = (scenario.feed(data) if name == 'scenario'
                                       else modem.feed(name, data))
                            for target, output in outputs.items():
                                if target not in active or target in eof:
                                    continue
                                if len(pending[target]) + len(output) > 65536:
                                    raise BufferError('AT channel output exceeded bounded queue')
                                pending[target].extend(output)
                                selector.modify(active[target], selectors.EVENT_READ | selectors.EVENT_WRITE, target)
                    if events & selectors.EVENT_WRITE and pending[name]:
                        try:
                            count = conn.send(pending[name])
                        except BlockingIOError:
                            count = 0
                        del pending[name][:count]
                    if not pending[name]:
                        if name in eof:
                            selector.unregister(conn)
                            del active[name]
                        else:
                            selector.modify(conn, selectors.EVENT_READ, name)
    finally:
        for name, conn in all_connections.items():
            conn.settimeout(original[name])
        if link_changed is not None and modem is not None and not modem.link_failed:
            link_changed(False)


def qmp_link(port, device):
    """Explicit local prototype attachment, not a controller ownership bypass."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('QMP port must be 1..65535')
    if not isinstance(device, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', device):
        raise ValueError('Explicit modem NIC device ID required')
    from uconsole_emulator import qmp
    return lambda up: qmp(port, 'set_link', {'name': device, 'up': up})


def serve_cli(connections, state, link_changed=None):
    """Stop the adapter before QEMU so its final link-down can be acknowledged."""
    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        try:
            serve_channels(connections, state, link_changed=link_changed)
        except KeyboardInterrupt:
            pass  # serve_channels has completed its link-down finally block.
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--sim', choices=('ready','absent','pin','puk'), default='ready')
    parser.add_argument('--secondary-port', type=int)
    parser.add_argument('--qmp-port', type=int)
    parser.add_argument('--network-device', help='Explicit QEMU modem device ID for radio gating')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or (args.secondary_port is not None and
                                     (not 1 <= args.secondary_port <= 65535 or args.secondary_port == args.port)):
        parser.error('port must be 1..65535')
    if (args.qmp_port is None) != (args.network_device is None):
        parser.error('--qmp-port and --network-device must be supplied together')
    link = qmp_link(args.qmp_port, args.network_device) if args.qmp_port is not None else None
    with ExitStack() as stack:
        connections = {'primary': stack.enter_context(socket.create_connection(('127.0.0.1', args.port), timeout=10))}
        if args.secondary_port is not None:
            connections['secondary'] = stack.enter_context(socket.create_connection(('127.0.0.1', args.secondary_port), timeout=10))
        serve_cli(connections, ModemState(sim=args.sim), link_changed=link)
