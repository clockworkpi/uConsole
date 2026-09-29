"""Qualify synthetic modem RNDIS with a diskless guest and local HTTP fixture."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import lzma
from pathlib import Path
import re
import shlex
import subprocess
import threading

from validate_recovery_guest_ssh import run as qualify_ram
from validate_recovery_ssh import client


def check_interface(text):
    lines = text.splitlines()
    if len(lines) != 3 or lines[0] != 'rndis_host':
        raise ValueError('Expected the Linux RNDIS driver')
    if not re.fullmatch(r'/sys/devices/.+/[^/]+:2\.5', lines[1]):
        raise ValueError('Expected modem configuration 2, control interface 5')
    if lines[2] != '1e0e:9001':
        raise ValueError('Network interface is not the modem')


def run(output, key, module, port, radio_gating=False):
    output = Path(output).absolute()
    qualify_ram(key, output, port)
    command = client(key, output / 'known_hosts', port)

    def ssh(script, data=None):
        command[-1] = script
        result = subprocess.run(command, input=data, capture_output=True, timeout=30)
        if result.returncode:
            (output / 'ssh-failure.json').write_text(json.dumps(dict(
                command=script, returncode=result.returncode,
                stdout=result.stdout.decode(errors='replace'),
                stderr=result.stderr.decode(errors='replace')), indent=2)+'\n')
            raise RuntimeError('Guest command failed with status ' + str(result.returncode))
        return result.stdout

    def radio(value):
        script = ('exec 3<>/dev/ttyUSB2; stty raw -echo <&3; '
                    "printf 'AT+CFUN=" + str(value) + "\\r' >&3; "
                    'timeout 10 dd bs=1 count=6 <&3')
        reply = ssh('sh -c ' + shlex.quote(script))
        if reply != b'\r\nOK\r\n':
            raise RuntimeError('Radio control acknowledgement differs')

    if radio_gating:
        # Run after composite serial qualification (PIN unlocked, echo off).
        radio(1)

    data = lzma.decompress(Path(module).read_bytes())
    digest = hashlib.sha256(data).hexdigest()
    result = ssh('set -e; cat > /run/rndis_host.ko; '
                 'if ! test -d /sys/module/rndis_host; then insmod /run/rndis_host.ko; fi; '
                 'sha256sum /run/rndis_host.ko', data)
    if result.decode().split() != [digest, '/run/rndis_host.ko']:
        raise RuntimeError('RNDIS module provenance differs')
    identity = ssh('set -e; p=$(readlink -f /sys/class/net/usb1/device); '
                   'basename "$(readlink -f "$p/driver")"; echo "$p"; '
                   'printf "%s:%s\\n" "$(cat "$p/../idVendor")" "$(cat "$p/../idProduct")"')
    (output / 'interface.txt').write_bytes(identity)
    check_interface(identity.decode())
    # Separate subnet prevents accidentally proving the management USB NIC.
    ssh('ip address replace 10.0.3.15/24 dev usb1 && ip link set usb1 up')
    payload = bytes(range(256)) * 1024
    payload_digest = hashlib.sha256(payload).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/fixture':
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = ssh('wget -q -O /run/modem-fixture http://10.0.3.2:' +
                     str(server.server_port) + '/fixture && sha256sum /run/modem-fixture')
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    if result.decode().split() != [payload_digest, '/run/modem-fixture']:
        raise RuntimeError('Modem network payload differs')
    if radio_gating:
        ssh('ping -I usb1 -c 1 -W 2 10.0.3.2')
        radio(4)
        result = ssh('ping -I usb1 -c 1 -W 2 10.0.3.2 >/run/radio-off-ping 2>&1; '
                     'status=$?; cat /run/radio-off-ping; echo FORGE_STATUS=$status')
        (output / 'radio-off-ping.txt').write_bytes(result)
        if b'FORGE_STATUS=1\n' not in result or b'100% packet loss' not in result:
            raise RuntimeError('Radio-off did not block a previously working network path')
        radio(1)
        result = ssh('ping -I usb1 -c 1 -W 2 10.0.3.2')
        (output / 'radio-restored-ping.txt').write_bytes(result)
    evidence = dict(status='passed', module_sha256=digest,
                    payload_sha256=payload_digest, payload_bytes=len(payload),
                    scope='Synthetic RNDIS USB NIC and local TCP transfer',
                    physical_descriptor_match_qualified=False,
                    radio_toggle_gating_qualified=radio_gating,
                    registration_fault_gating_qualified=False)
    (output / 'network-acceptance.json').write_text(json.dumps(evidence, indent=2)+'\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--module', required=True, type=Path)
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--radio-gating', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.key, args.module, args.port, args.radio_gating), indent=2))
