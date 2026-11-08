#!/usr/bin/env python3
"""Exercise a native Linux GENET driver through DHCP and real packet transfers.

Requires a matching Linux reference kernel/root/DTB with dhcpcd, Python3 and
ethtool. Uses immutable regular inputs, an isolated overlay and loopback-only
restricted user networking. This is a BCM2711 model control, not physical
hardware, full-memory stress, Pi5/RP1 or generic Linux compatibility acceptance.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import time
import urllib.request
import uuid


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def until(console, marker, timeout):
    deadline = time.monotonic() + timeout
    output = b''
    while marker not in output:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f'guest marker absent: {marker!r}')
        console.settimeout(remaining)
        block = console.recv(65536)
        if not block:
            raise RuntimeError(f'guest UART EOF before {marker!r}')
        output += block
        if len(output) > 8 * 1024 * 1024:
            raise RuntimeError('UART command output exceeds bound')
    return output


def python_command(source):
    encoded = base64.b64encode(source.encode()).decode()
    return ('python3 -u -c ' + shlex.quote("exec(__import__('base64').b64decode('" + encoded + "'))") + '\n').encode()



SERVER_TEMPLATE = r"""
import hashlib,json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
token=TOKEN
size=SIZE
payload=bytes((i*17+29)%256 for i in range(256))*(size//256)
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path!='/'+token+'/payload': self.send_error(404);return
  self.send_response(200);self.send_header('Content-Length',str(size));self.end_headers();self.wfile.write(payload)
 def do_POST(self):
  if self.path!='/'+token+'/upload' or self.headers.get('Content-Length')!=str(size): self.send_error(400);return
  data=self.rfile.read(size)
  sha=hashlib.sha256(data).hexdigest()
  if len(data)!=size or sha!=POST_HASH: self.send_error(422);return
  with open('/run/genet-upload.bin','wb') as f: f.write(data)
  reply=json.dumps(dict(token=token,bytes=len(data),sha256=sha)).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(reply)));self.end_headers();self.wfile.write(reply)
http=HTTPServer(('0.0.0.0',8080),Handler)
print('GENET_HTTP_READY_'+token,flush=True)
http.serve_forever()
""".lstrip("\n")

PROBE_TEMPLATE = r"""
import hashlib,json,os,pathlib,subprocess
p=pathlib.Path('/sys/class/net/eth0')
config=pathlib.Path('/boot/config-6.12.62-v8+').read_text()
report=dict(kernel=os.uname().release,driver=os.path.basename(os.path.realpath(p/'device/driver')),
 online=pathlib.Path('/sys/devices/system/cpu/online').read_text().strip(),
 meminfo=pathlib.Path('/proc/meminfo').read_text().splitlines()[0],
 carrier=(p/'carrier').read_text().strip(),
 phy_id=(p/'phydev/phy_id').read_text().strip(),
 interrupts=pathlib.Path('/proc/interrupts').read_text(),
 stats={k:int((p/'statistics'/k).read_text()) for k in ['rx_packets','tx_packets','rx_bytes','tx_bytes','rx_errors','tx_errors']},
 config=[x for x in config.splitlines() if x.startswith(('CONFIG_BCMGENET=','CONFIG_BROADCOM_PHY=','CONFIG_BCM7XXX_PHY='))],
 uploaded_sha256=hashlib.sha256(pathlib.Path('/run/genet-upload.bin').read_bytes()).hexdigest(),
 ip=subprocess.check_output(['ip','-j','addr','show','dev','eth0'],text=True),
 ethtool=subprocess.check_output(['ethtool','-i','eth0'],text=True),
 link_modes=subprocess.check_output(['ethtool','eth0'],text=True))
print()
print('GENET_RESULT_JSON='+json.dumps(report))
print('GENET_RESULT_END')
""".lstrip("\n")

def server_source(token, size, expected_upload):
    return (SERVER_TEMPLATE.replace('TOKEN', repr(token)).replace('SIZE', str(size))
            .replace('POST_HASH', repr(expected_upload)))


def probe_source(kernel_release):
    return PROBE_TEMPLATE.replace('/boot/config-6.12.62-v8+', '/boot/config-' + kernel_release)


def assess_guest(report, machine, kernel_release, expected_upload, size):
    addresses = json.loads(report['ip'])
    leased = any(a.get('family')=='inet' and a.get('local')=='10.0.2.15'
                 for interface in addresses for a in interface.get('addr_info', []))
    irq_lines = [line for line in report['interrupts'].splitlines()
                 if re.search(r'\beth0\b|genet|fd580000', line, re.I)]
    checks = dict(native_driver=report['driver']=='bcmgenet', native_kernel=report['kernel']==kernel_release,
                  dhcp_address=leased,
                  native_link=report['carrier']=='1',
                  native_phy=int(report['phy_id'],16)==0x600d84a2,
                  native_interrupts=any(any(int(v)>0 for v in line.split(':',1)[1].split()[:4])
                                        for line in irq_lines),
                  four_cpus=report['online']=='0-3', built_in_driver='CONFIG_BCMGENET=y' in report['config'],
                  guest_memory=int(report['meminfo'].split()[1])*1024 >=
                               (({'raspi4b':2,'raspi4b-4g':4,'raspi4b-8g':8}[machine]-1)<<30),
                  uploaded_payload=report['uploaded_sha256']==expected_upload,
                  rx_over_one_ring=report['stats']['rx_packets']>256,
                  tx_over_one_ring=report['stats']['tx_packets']>256,
                  bidirectional_bytes=report['stats']['rx_bytes']>size and report['stats']['tx_bytes']>size,
                  error_counters_clean=report['stats']['rx_errors']==report['stats']['tx_errors']==0)
    return checks, irq_lines


def console_connection(path, process):
    # Prefer the observable wait when the controller supplies toolscheme.
    # The existing portable connector handles the bind/listen race afterward.
    if shutil.which('toolscheme'):
        condition = '(wait-for (quote (exists ' + json.dumps(str(path)) + ')) (quote ((timeout-ms 10000))))'
        subprocess.run(['toolscheme', '-e', condition], check=True, timeout=12, cwd=path.parent)
    from test_emulator_pmic import connect
    return connect(path, process)


def run(args):
    paths = {name: getattr(args, name).resolve() for name in ('image','kernel','dtb','qemu','qemu_img')}
    inputs = list(paths.values())
    for name, path in paths.items():
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f'input must be a regular file: {path}')
        if digest(path) != getattr(args, name + '_sha256').lower():
            raise ValueError(f'SHA-256 mismatch: {path}')
        setattr(args, name, path)
    if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,80}', args.kernel_release):
        raise ValueError('kernel release must be a plain release identifier')

    args.output.mkdir(mode=0o700)
    out = args.output.resolve()
    token = uuid.uuid4().hex
    size = 1024 * 1024
    download = bytes((i * 17 + 29) % 256 for i in range(256)) * (size // 256)
    upload = bytes((i * 31 + 7) % 256 for i in range(256)) * (size // 256)
    get_hash, post_hash = hashlib.sha256(download).hexdigest(), hashlib.sha256(upload).hexdigest()
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    env = dict(os.environ)
    env.pop('LD_LIBRARY_PATH', None)
    env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin'
    manifest = {'schema': 'uconsole/pi4-genet-linux@1', 'ok': False,
                'harness_sha256': digest(Path(__file__).resolve()),
                'console_helper_sha256': digest(Path(__file__).with_name('test_emulator_pmic.py')),
                'inputs': {str(p): digest(p) for p in inputs}, 'machine': args.machine,
                'http_payload_bytes_each_direction': size, 'hostfwd': f'127.0.0.1:{port}',
                'network_restricted': True, 'token': token}
    subprocess.run([str(args.qemu_img), 'create', '-f', 'qcow2', '-F', 'raw', '-b',
                    str(args.image), str(out / 'disk.qcow2'), '8G'], check=True, env=env)
    command = [str(args.qemu), '-machine', args.machine, '-accel', 'tcg',
               '-global', f'{args.peripheral_type}.genet-network=on',
               '-netdev', f'user,id=genet0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:8080',
               '-global', f'{args.nic_type}.netdev=genet0',
               '-global', f'{args.nic_type}.phy-addr={args.phy_addr}',
               '-global', f'{args.nic_type}.mac=52:54:00:12:34:56', '-nic', 'none',
               '-kernel', str(args.kernel), '-dtb', str(args.dtb),
               '-drive', f'if=sd,format=qcow2,file={out / "disk.qcow2"}',
               '-append', 'earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1,115200 '
               'root=PARTUUID=21965b0c-02 rootfstype=ext4 rootwait rw init=/bin/bash',
               '-display', 'none', '-chardev', 'socket,id=console,path=serial.sock,server=on,wait=off,logfile=serial.log',
               '-serial', 'chardev:console', '-monitor', 'none',
               '-d', 'guest_errors,unimp', '-D', 'guest-errors.log']
    manifest['command'] = command
    (out / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    process = None
    try:
        with (out / 'qemu.log').open('wb') as log:
            process = subprocess.Popen(command, stdout=log, stderr=log, env=env, cwd=out)
            manifest['pid'] = process.pid
            with console_connection(out / 'serial.sock', process) as console:
                (out / 'boot-output.txt').write_bytes(until(console, b'root@(none):/#', 180))
                console.sendall(b'stty -echo; export PATH=/usr/sbin:/usr/bin:/sbin:/bin; '
                                b'mount -t proc proc /proc; mount -t sysfs sysfs /sys; '
                                b'mount -t tmpfs tmpfs /run; ip link set eth0 up; '
                                b'dhcpcd -4 -w -t 30 -L eth0; echo DHCP_COMMAND_FINISHED\n')
                (out / 'dhcp-output.txt').write_bytes(until(console, b'\r\nDHCP_COMMAND_FINISHED\r\n', 60))
                server = server_source(token, size, post_hash)
                manifest['guest_server_sha256'] = hashlib.sha256(server.encode()).hexdigest()
                (out / 'guest-server.py').write_text(server)
                console.sendall(python_command(server).rstrip(b'\n') + b' &\nGENET_HTTP_PID=$!\n')
                (out / 'http-ready.txt').write_bytes(until(console, ('GENET_HTTP_READY_' + token + '\r\n').encode(), 20))
                # Ignore proxy settings: only the explicitly forwarded localhost endpoint is used.
                http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with http.open(f'http://127.0.0.1:{port}/{token}/payload', timeout=30) as response:
                    received = response.read(size + 1)
                if len(received) != size or hashlib.sha256(received).hexdigest() != get_hash:
                    raise RuntimeError('native guest-to-host payload mismatch')
                request = urllib.request.Request(f'http://127.0.0.1:{port}/{token}/upload', data=upload, method='POST')
                with http.open(request, timeout=30) as response:
                    reply = json.loads(response.read(4096))
                if reply != {'token': token, 'bytes': size, 'sha256': post_hash}:
                    raise RuntimeError('native host-to-guest upload readback mismatch')
                manifest['transfers'] = {'guest_to_host_sha256': get_hash, 'host_to_guest_sha256': post_hash,
                                         'upload_ack': reply}
                console.sendall(b'kill "$GENET_HTTP_PID"\n')
                probe = probe_source(args.kernel_release)
                manifest['guest_probe_sha256'] = hashlib.sha256(probe.encode()).hexdigest()
                (out / 'guest-probe.py').write_text(probe)
                console.sendall(python_command(probe))
                raw = until(console, b'\r\nGENET_RESULT_END\r\n', 30)
                (out / 'network-output.txt').write_bytes(raw)
                match = re.search(rb'^GENET_RESULT_JSON=(.+)\r?$', raw, re.M)
                if not match:
                    raise RuntimeError('native driver result missing')
                report = json.loads(match[1]); manifest['guest'] = report
                checks, irq_lines = assess_guest(report, args.machine, args.kernel_release, post_hash, size)
                manifest['native_nic_irq_lines'] = irq_lines
                console.sendall(b'dhcpcd -x eth0; sync; mount -o remount,ro /; echo ROOT_READONLY; grep " / " /proc/mounts; exec /sbin/poweroff -f\n')
                ending = until(console, b'reboot: Power down', 30)
                (out / 'powerdown-output.txt').write_bytes(ending)
                checks['root_readonly'] = bool(re.search(rb' / ext4 ro[, ]', ending))
                checks['guest_powerdown'] = b'reboot: Power down' in ending
                manifest['exit_code'] = process.wait(timeout=30)
                checks['natural_qemu_exit'] = manifest['exit_code']==0
                checks['inputs_unchanged'] = all(digest(Path(p))==sha for p,sha in manifest['inputs'].items())
                manifest['checks'] = checks; manifest['ok'] = all(checks.values())
                if not manifest['ok']:
                    raise RuntimeError(f'actual network checks incomplete: {checks}')
                print('PASS: native Linux GENET bidirectional one-MiB HTTP, ring wrap and clean powerdown')
    except BaseException as error:
        manifest['error'] = str(error)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        manifest['inputs_unchanged'] = all(digest(Path(p))==sha for p,sha in manifest['inputs'].items())
        (out / 'result.json').write_text(json.dumps(manifest, indent=2) + '\n')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image','kernel','dtb','qemu','qemu-img'):
        parser.add_argument('--' + name, type=Path, required=True)
        parser.add_argument('--' + name + '-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--machine', choices=['raspi4b','raspi4b-4g','raspi4b-8g'], default='raspi4b')
    parser.add_argument('--phy-addr', type=int, choices=range(32), default=0,
                        help='explicit CM4 PHY strap; use1 with a separately enrolled Pi4B DTB')
    parser.add_argument('--kernel-release', default='6.12.62-v8+',
                        help='expected running reference kernel and /boot/config-RELEASE')
    args = parser.parse_args()
    args.peripheral_type, args.nic_type = 'bcm2838-peripherals', 'bcm2838-genet'
    run(args)


if __name__ == '__main__':
    main()
