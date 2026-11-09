"""Read-only CM4 SD layout binding; no raw-write or restore authorization."""
import base64
import hashlib
import re
import struct


READER = r'''import base64,fcntl,json,os,stat,struct
from pathlib import Path
disk=Path('/sys/class/block/mmcblk0')
def inventory():
    parts=[]
    for path in sorted(disk.glob('mmcblk0p*')):
        if not (path/'partition').is_file(): continue
        parts.append(dict(number=int((path/'partition').read_text()),
                          start=int((path/'start').read_text()),sectors=int((path/'size').read_text())))
    return dict(cid=(disk/'device/cid').read_text().strip(),
                sectors=int((disk/'size').read_text()),partitions=parts,
                dev=(disk/'dev').read_text().strip())
before=inventory()
fd=os.open('/dev/mmcblk0',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
    info=os.fstat(fd)
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'!=before['dev']:
        raise ValueError('SD device identity differs')
    size=bytearray(8)
    fcntl.ioctl(fd,0x80081272,size,True)
    sector=bytearray(4)
    fcntl.ioctl(fd,0x1268,sector,True)
    header=os.pread(fd,512,0)
    if inventory()!=before or os.pread(fd,512,0)!=header:
        raise ValueError('SD layout changed during observation')
    before.update(device='/dev/mmcblk0',bytes=struct.unpack('=Q',size)[0],
                  sector_size=struct.unpack('=I',sector)[0],mbr=base64.b64encode(header).decode())
finally:
    os.close(fd)
print(json.dumps(before))
'''


def reader(device='/dev/mmcblk0'):
    if not isinstance(device, str) or not re.fullmatch(r'/dev/mmcblk[0-9]{1,2}', device):
        raise ValueError('Expected a whole MMC device, not a partition or arbitrary path')
    return READER.replace('mmcblk0', device.removeprefix('/dev/'))


def root_extent(observed, expected_cid, expected_disk_id, *, device='/dev/mmcblk0'):
    """Validate this target's two-primary-partition DOS layout before planning.

    Supporting another layout needs an explicit planner, not guessed offsets.
    Observation of a live system does not establish backup consistency.
    """
    reader(device)
    if not isinstance(expected_cid, str) or not re.fullmatch('[0-9a-f]{32}', expected_cid):
        raise ValueError('Invalid expected SD identity')
    if not isinstance(expected_disk_id, str) or not re.fullmatch('[0-9a-f]{8}', expected_disk_id):
        raise ValueError('Invalid expected DOS disk ID')
    if (observed.get('device') != device or observed.get('cid') != expected_cid or
            type(observed.get('sector_size')) is not int or observed['sector_size'] != 512 or
            type(observed.get('sectors')) is not int or observed['sectors'] <= 0 or
            type(observed.get('bytes')) is not int or observed['bytes'] != observed['sectors']*512):
        raise ValueError('SD identity or geometry differs')
    header = base64.b64decode(observed['mbr'], validate=True)
    if len(header) != 512 or header[510:] != b'\x55\xaa':
        raise ValueError('Invalid DOS partition header')
    if format(struct.unpack_from('<I', header, 440)[0], '08x') != expected_disk_id:
        raise ValueError('DOS disk identity differs')
    partitions = []
    for number in range(1, 5):
        entry = header[446+(number-1)*16:446+number*16]
        flag, kind = entry[0], entry[4]
        start, count = struct.unpack_from('<II', entry, 8)
        if number > 2:
            if any(entry):
                raise ValueError('Additional partitions require a separate restore planner')
            continue
        if (flag not in (0, 0x80) or kind != (0x0c if number == 1 else 0x83) or
                start < 1 or count < 1 or start+count > observed['sectors']):
            raise ValueError('Unexpected boot/root partition geometry or type')
        partitions.append(dict(number=number, start=start, sectors=count))
    boot, root = partitions
    if boot['start']+boot['sectors'] > root['start']:
        raise ValueError('Boot and root partitions overlap or are reversed')
    if observed.get('partitions') != partitions:
        raise ValueError('Kernel partition inventory differs from DOS header')
    return dict(kind='root-partition-extent', cid=expected_cid, disk_id=expected_disk_id,
                mbr_sha256=hashlib.sha256(header).hexdigest(), device=device+'p2',
                offset_bytes=root['start']*512, length_bytes=root['sectors']*512,
                protected_prefix_bytes=root['start']*512,
                protected_suffix_start_bytes=(root['start']+root['sectors'])*512,
                disk_bytes=observed['bytes'], restore_authorized=False,
                consistent_backup_qualified=False, whole_card_write_authorized=False)
