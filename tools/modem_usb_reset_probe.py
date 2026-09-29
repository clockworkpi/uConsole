"""Guest-only reset of the exact synthetic modem USB device, never a host USB device."""

PROBE = r'''import fcntl, json, os, pathlib, time
devices = []
for device in pathlib.Path('/sys/bus/usb/devices').iterdir():
    vendor, product = device/'idVendor', device/'idProduct'
    if vendor.is_file() and product.is_file() and vendor.read_text().strip() == '1e0e' and product.read_text().strip() == '9001':
        devices.append(device)
if len(devices) != 1:
    raise RuntimeError('Expected exactly one synthetic modem before USB reset')
device = devices[0]
if ((device/'manufacturer').read_text().strip() != 'Forge' or
        (device/'product').read_text().strip() != 'SIM7600 composite prototype'):
    raise RuntimeError('Refusing reset of a non-Forge USB modem')
bus = int((device/'busnum').read_text())
number = int((device/'devnum').read_text())
node = '/dev/bus/usb/%03d/%03d' % (bus, number)
fd = os.open(node, os.O_WRONLY)
try:
    # Linux USBDEVFS_RESET = _IO('U', 20), with no pointer argument.
    fcntl.ioctl(fd, (ord('U') << 8) | 20, 0)
finally:
    os.close(fd)
deadline = time.monotonic()+10
while True:
    serial = list(pathlib.Path('/sys/class/tty').glob('ttyUSB*'))
    network = [p.name for p in pathlib.Path('/sys/class/net').iterdir()
               if (p/'device'/'driver').resolve().name == 'rndis_host']
    if len(serial) == 5 and len(network) == 1:
        break
    if time.monotonic() > deadline:
        raise RuntimeError('USB modem re-enumeration deadline')
    time.sleep(0.05)
print(json.dumps(dict(status='reset-completed', node=node, serial=[p.name for p in serial], network=network)))
'''
