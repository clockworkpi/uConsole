"""Diskless guest registration-fault qualification using private scenario IPC."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import shlex
import socket
import subprocess
import threading

from forge_modem_at import ModemState
from serve_modem_at import qmp_link, serve_channels
from validate_modem_composite_guest import run as serial_test
from validate_modem_network_guest import run as network_test
from validate_recovery_ssh import client


def check_transition(at, ping, registration, up):
    expected = ('\r\n+CEREG: 0,%d\r\nOK\r\n' % registration).encode()
    if at.returncode or at.stdout != expected:
        raise RuntimeError('Guest registration query differs')
    if ping.returncode != (0 if up else 1) or (not up and b'100% packet loss' not in ping.stdout):
        raise RuntimeError('Guest packet path disagrees with registration state')


def run(output, key, modules, ssh_port, primary_port, secondary_port, qmp_port):
    output.mkdir(mode=0o700)
    errors = []
    evidence = []
    with ExitStack() as stack:
        connections = {name: stack.enter_context(socket.create_connection(('127.0.0.1', port), timeout=10))
                       for name, port in (('primary', primary_port), ('secondary', secondary_port))}
        owner, adapter = socket.socketpair()
        stack.enter_context(owner)
        stack.enter_context(adapter)
        owner.settimeout(10)

        def serve():
            try:
                serve_channels(connections, ModemState(sim='pin'),
                               qmp_link(qmp_port, 'modem-device'), adapter)
            except Exception as exc:
                errors.append(repr(exc))

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            serial_test(output/'serial', key, modules, ssh_port)
            network_test(output/'network', key, modules/'rndis_host.ko.xz', ssh_port, True)
            command = client(key, output/'network'/'known_hosts', ssh_port)
            cases = [(dict(registration=3), 3, False),
                     (dict(registration=5), 5, True),
                     (dict(registration=2), 2, False),
                     (dict(registration=0), 0, False),
                     (dict(registration=1), 1, True),
                     (dict(sim='absent'), 0, False),
                     (dict(sim='ready'), 1, True)]
            for sequence, (changes, registration, up) in enumerate(cases, 1):
                owner.sendall((json.dumps(dict(sequence=sequence, changes=changes))+'\n').encode())
                response = bytearray()
                while not response.endswith(b'\n'):
                    chunk = owner.recv(1)
                    if not chunk or len(response) >= 4096:
                        raise RuntimeError('Missing or oversized scenario acknowledgement')
                    response.extend(chunk)
                ack = json.loads(response)
                if (ack.get('sequence') != sequence or ack.get('status') != 'applied' or
                        ack['observed']['registration'] != registration or
                        ack['observed']['link_up'] is not up):
                    raise RuntimeError('Scenario acknowledgement differs')
                expected = ('\r\n+CEREG: 0,%d\r\nOK\r\n' % registration).encode()
                script = ('exec 3<>/dev/ttyUSB2; stty raw -echo <&3; '
                          "printf 'AT+CEREG?\\r' >&3; timeout 10 dd bs=1 count=" +
                          str(len(expected)) + ' <&3')
                command[-1] = 'sh -c ' + shlex.quote(script)
                at = subprocess.run(command, capture_output=True, timeout=20)
                command[-1] = 'ping -I usb1 -c 1 -W 2 10.0.3.2'
                ping = subprocess.run(command, capture_output=True, timeout=10)
                record = dict(sequence=sequence, changes=changes, acknowledgement=ack,
                              at_status=at.returncode, at=at.stdout.decode(errors='replace'),
                              at_error=at.stderr.decode(errors='replace'),
                              ping_status=ping.returncode, ping=ping.stdout.decode(errors='replace'),
                              ping_error=ping.stderr.decode(errors='replace'))
                evidence.append(record)
                (output/'transitions.json').write_text(json.dumps(evidence, indent=2)+'\n')
                check_transition(at, ping, registration, up)
        finally:
            # End adapter inputs while QEMU is alive, allowing its final QMP
            # link-down acknowledgement before the caller stops the VM.
            owner.shutdown(socket.SHUT_WR)
            for conn in connections.values():
                conn.shutdown(socket.SHUT_RD)
            worker.join(15)
            (output/'adapter-shutdown.json').write_text(json.dumps(
                dict(stopped=not worker.is_alive(), errors=errors), indent=2)+'\n')
            if worker.is_alive() or errors:
                raise RuntimeError('Adapter shutdown did not complete cleanly')
    result = dict(status='passed', transitions=len(evidence),
                  scope='Synthetic registration/SIM state, guest AT queries and RNDIS packet gating',
                  physical_modem_qualified=False, pdp_contexts_qualified=False)
    (output/'acceptance.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('output', 'key', 'modules'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('ssh-port', 'primary-port', 'secondary-port', 'qmp-port'):
        parser.add_argument('--'+name, type=int, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.key, args.modules, args.ssh_port,
                         args.primary_port, args.secondary_port, args.qmp_port), indent=2))
