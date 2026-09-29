"""Guest-side literal PDP acceptance oracle; independent of the AT engine."""

PROBE = r'''import json, os, pathlib, select, subprocess, termios, time, tty
ports = [os.open('/dev/ttyUSB%d' % n, os.O_RDWR | os.O_NONBLOCK | os.O_NOCTTY) for n in (2, 3)]
records = []
try:
    for fd in ports:
        tty.setraw(fd)
        termios.tcflush(fd, termios.TCIOFLUSH)
    def exchange(port, command, expected):
        fd = ports[port]
        os.write(fd, command.encode() + b'\r')
        result = bytearray()
        deadline = time.monotonic()+10
        while not (result.endswith(b'\r\nOK\r\n') or result.endswith(b'\r\nERROR\r\n')):
            remaining = deadline-time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                raise RuntimeError('AT timeout: ' + command)
            result.extend(os.read(fd, 1024))
            if len(result) > 4096:
                raise RuntimeError('AT response overflow')
        if bytes(result) not in expected:
            raise RuntimeError('Unexpected AT response: ' + repr(bytes(result)))
        records.append(dict(command=command, port=port, response=result.decode()))
    for port in (0, 1):
        exchange(port, 'ATE0', (b'ATE0\r\r\nOK\r\n', b'\r\nOK\r\n'))
    # Stop registration URCs before inspecting exact command transcripts.
    exchange(1, 'AT+CEREG=0', (b'\r\nOK\r\n',))
    interfaces = []
    for net in pathlib.Path('/sys/class/net').iterdir():
        device = (net/'device').resolve()
        if (device/'driver').resolve().name == 'rndis_host' and (device.parent/'idVendor').read_text().strip() == '1e0e':
            interfaces.append(net.name)
    if len(interfaces) != 1:
        raise RuntimeError('Expected exactly one modem RNDIS interface')
    interface = interfaces[0]
    subprocess.run(['ip','addr','replace','10.0.3.15/24','dev',interface], check=True)
    subprocess.run(['ip','link','set',interface,'up'], check=True)
    def packet(up):
        result = subprocess.run(['ping','-I',interface,'-c','1','-W','2','10.0.3.2'], capture_output=True, text=True, timeout=5)
        if result.returncode != (0 if up else 1) or (not up and '100% packet loss' not in result.stdout):
            raise RuntimeError('PDP packet state differs: ' + result.stdout + result.stderr)
        records.append(dict(packet_up=up, status=result.returncode, stdout=result.stdout))
    ok = (b'\r\nOK\r\n',)
    packet(True)
    exchange(0, 'AT+CGACT=0,1', ok)
    packet(False)
    exchange(1, 'AT+CGDCONT=1,"IP","Forge.Test"', ok)
    exchange(0, 'AT+CGDCONT?', (b'\r\n+CGDCONT: 1,"IP","Forge.Test","",0,0,0,0\r\nOK\r\n',))
    exchange(1, 'AT+CGACT=1,1', ok)
    exchange(0, 'AT+CGACT?', (b'\r\n+CGACT: 1,1\r\nOK\r\n',))
    packet(True)
    exchange(0, 'AT+CGATT=0', ok)
    exchange(1, 'AT+CGACT?', (b'\r\n+CGACT: 1,0\r\nOK\r\n',))
    packet(False)
    exchange(1, 'AT+CGACT=1,1', (b'\r\nERROR\r\n',))
    exchange(0, 'AT+CGATT=1', ok)
    packet(False)
    exchange(1, 'AT+CGACT=1,1', ok)
    packet(True)
    print(json.dumps(dict(status='passed', interface=interface, records=records)))
finally:
    for fd in ports:
        os.close(fd)
'''
