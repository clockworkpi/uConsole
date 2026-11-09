"""Diskless guest qualification for Forge's five-interface serial prototype."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import re
import shlex
import subprocess

from validate_recovery_guest_ssh import run as qualify_ram
from validate_recovery_ssh import client


def check_interfaces(text, configuration=2):
    if type(configuration) is not int or configuration not in (1, 2):
        raise ValueError('Unsupported modem configuration')
    lines = text.splitlines()
    if len(lines) != 5:
        raise ValueError('Expected exactly five option-driver serial interfaces')
    devices = set()
    for index, line in enumerate(lines):
        match = re.fullmatch(r'ttyUSB([0-4]) (.+/([^/]+):' + str(configuration) +
                            r'\.([0-4])/ttyUSB([0-4])) option1', line)
        if not match or any(int(match[group]) != index for group in (1,4,5)):
            raise ValueError('Serial role/interface/driver mapping differs')
        devices.add(match[2].rsplit(':' + str(configuration) + '.',1)[0])
    if len(devices) != 1:
        raise ValueError('Serial ports are not one composite USB device')


def run(output, key, modules, port):
    output = Path(output).absolute()
    qualify_ram(key, output, port)
    command = client(key, output / 'known_hosts', port)
    def ssh(script, data=None):
        command[-1] = script
        return subprocess.run(command, input=data, capture_output=True, timeout=25)
    hashes = []
    for name in ('usbserial','usb_wwan','option'):
        data = lzma.decompress((modules / (name + '.ko.xz')).read_bytes())
        digest = hashlib.sha256(data).hexdigest()
        result = ssh('if ! test -d /sys/module/'+name+'; then cat > /run/'+name+
                     '.ko && insmod /run/'+name+'.ko || exit 1; fi; sha256sum /run/'+name+'.ko', data)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
        if result.stdout.decode().split() != [digest, '/run/'+name+'.ko']:
            raise RuntimeError('Guest module fixture provenance differs')
        hashes.append(dict(name=name, sha256=digest))
    script = '''for p in /sys/class/tty/ttyUSB*; do
        printf '%s %s %s\n' "${p##*/}" "$(readlink -f "$p/device")" "$(basename "$(readlink -f "$p/device/driver")")"
    done'''
    result = ssh(script)
    (output / 'interfaces.txt').write_bytes(result.stdout)
    if result.returncode:
        raise RuntimeError('Guest interface capture failed')
    check_interfaces(result.stdout.decode())
    exchanges = [
        (3, 'ATE0\r', b'ATE0\r\r\nOK\r\n'),
        (4, 'ATE0\rAT+CEREG=1\r', b'ATE0\r\r\nOK\r\n\r\nOK\r\n'),
        (3, 'AT+CPIN?\r', b'\r\n+CPIN: SIM PIN\r\nOK\r\n'),
        (3, 'AT+CPIN="1234"\r', b'\r\nOK\r\n'),
        (4, '', b'\r\n+CEREG: 1\r\n'),
        (4, 'AT+CFUN=4\r', b'\r\nOK\r\n\r\n+CEREG: 0\r\n'),
        (3, 'AT+CFUN?\r', b'\r\n+CFUN: 4\r\nOK\r\n'),
    ]
    # option/usb_wwan has no configurable physical baud clock. Configure only
    # the tty line discipline; requiring a baud-rate readback is inappropriate.
    script = 'set -e; stty -F /dev/ttyUSB2 raw -echo; stty -F /dev/ttyUSB3 raw -echo; exec 3<>/dev/ttyUSB2; exec 4<>/dev/ttyUSB3; '
    for fd, request, expected in exchanges:
        if request:
            script += 'printf %s ' + shlex.quote(request) + ' >&' + str(fd) + '; '
        script += 'timeout 15 dd bs=1 count=' + str(len(expected)) + ' <&' + str(fd) + ' 2>/dev/null; '
    result = ssh('sh -c ' + shlex.quote(script))
    (output / 'transcript.json').write_text(json.dumps(dict(returncode=result.returncode,
        stdout=result.stdout.decode(errors='replace'), stderr=result.stderr.decode(errors='replace')), indent=2)+'\n')
    if result.returncode or result.stdout != b''.join(item[2] for item in exchanges):
        raise RuntimeError('Composite AT transcript differs')
    evidence = dict(status='passed', modules=hashes, serial_interfaces=5,
                    primary_interface=2, secondary_interface=3, cross_port_notifications=True,
                    scope='Composite serial prototype, not full SIM7600 USB/network equivalence',
                    packet_networking_qualified=False, physical_descriptor_match_qualified=False)
    (output/'modem-acceptance.json').write_text(json.dumps(evidence,indent=2)+'\n')
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--key', required=True, type=Path)
    parser.add_argument('--modules', required=True, type=Path)
    parser.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.output,args.key,args.modules,args.port),indent=2))
