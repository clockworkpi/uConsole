"""Refresh direct-boot artifacts from the stopped guest's current disk state."""
import gzip
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

from emulator_dtb import patch_strings
from emulator_image import BootPartition
from forge_workspace import sha256, sync_directory, sync_file


CM4_PATCH = {
    ('/soc/usb@7e980000', 'status'): 'okay',
    ('/soc/usb@7e980000', 'compatible'): 'brcm,bcm2835-usb',
    ('/soc/usb@7e980000', 'dr_mode'): 'host',
    ('/soc/serial@7e201000/bluetooth', 'status'): 'disabled',
    ('/soc/serial@7e201000', 'skip-init'): None,
    ('/soc/serial@7e201000', 'uart-has-rtscts'): None,
}


def boot_profile(text):
    """Interpret the supported CM4 firmware-selection subset, failing closed.

    This is not a firmware emulator. Unknown filters and include directives
    require explicit support instead of silently selecting a stale kernel.
    """
    active = True
    profile = {'kernel': 'kernel8.img', 'device_tree': 'bcm2711-rpi-cm4.dtb'}
    # Model filters and the firmware's 98-character line limit are documented at
    # https://www.raspberrypi.com/documentation/computers/config_txt.html
    for raw in text.splitlines():
        line = raw[:98].split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('['):
            if line not in ('[all]', '[pi4]', '[cm4]', '[pi0]', '[pi0w]', '[pi02]',
                            '[pi1]', '[pi2]', '[pi3]', '[pi3+]', '[pi400]', '[pi5]',
                            '[pi500]', '[cm0]', '[cm1]', '[cm3]', '[cm3+]', '[cm4s]',
                            '[cm5]', '[none]'):
                raise ValueError(f'Unsupported firmware filter: {line}')
            active = line in ('[all]', '[pi4]', '[cm4]')
            continue
        if not active:
            continue
        if line.startswith(('include ', 'initramfs ')):
            raise ValueError(f'Unsupported direct-boot directive: {line}')
        key, sep, value = line.partition('=')
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if key in profile:
            if not value or value.startswith('/') or '..' in value.split('/'):
                raise ValueError(f'Invalid boot file selection: {line}')
            profile[key] = value
        elif ((key in ('os_prefix', 'overlay_prefix') and value) or
              (key == 'arm_64bit' and value != '1') or
              (key in ('auto_initramfs', 'ramfsfile') and value not in ('', '0'))):
            raise ValueError(f'Unsupported direct-boot directive: {line}')
    return profile


def validate_kernel(path):
    with path.open('rb') as stream:
        compressed = stream.read(2) == b'\x1f\x8b'
    opener = gzip.open if compressed else open
    try:
        with opener(path, 'rb') as stream:
            header = stream.read(64)
            if len(header) != 64 or header[56:60] != b'ARM\x64':
                raise ValueError('Selected kernel is not an ARM64 Linux Image')
            # Read the full stream to verify gzip CRC/truncation, with a bound
            # against corrupt or hostile compressed boot artifacts.
            total = len(header)
            while chunk := stream.read(1024 * 1024):
                total += len(chunk)
                if total > 512 * 1024 * 1024:
                    raise ValueError('Kernel exceeds supported 512 MiB limit')
    except (OSError, EOFError) as exc:
        raise ValueError(f'Invalid compressed kernel: {exc}') from exc


def refresh_boot(workspace, qemu_img):
    """Caller must hold WorkspaceLock throughout refresh and the ensuing boot.

    Read only the MBR + boot partition through qemu-img, so this includes qcow2
    writes without mounting the guest or copying its entire root filesystem.
    Never use force-share: an externally running QEMU must make refresh fail.
    """
    workspace = Path(workspace).resolve()
    config = json.loads((workspace / 'machine.json').read_text())
    with tempfile.TemporaryDirectory(prefix='.boot-refresh-', dir=workspace) as directory:
        stage = Path(directory)

        def read_prefix(destination, sectors):
            block = 512 if sectors == 1 else 1024 * 1024
            count = (sectors * 512 + block - 1) // block
            subprocess.run([str(qemu_img), 'dd', '-f', 'qcow2', '-O', 'raw',
                            f'bs={block}', f'count={count}', f'if={workspace / "disk.qcow2"}',
                            f'of={destination}'], check=True, capture_output=True, text=True)

        read_prefix(stage / 'mbr', 1)
        mbr = (stage / 'mbr').read_bytes()
        if len(mbr) != 512 or mbr[510:] != b'\x55\xaa' or mbr[450] not in (6, 14, 11, 12):
            raise ValueError('Unsupported guest boot layout: expected MBR with first FAT partition')
        start, sectors = struct.unpack_from('<II', mbr, 454)
        if start < 1 or sectors < 1 or start + sectors > 4 * 1024 * 1024:
            raise ValueError('Unsupported guest boot partition: prefix must fit within 2 GiB')
        if mbr[466] != 0x83:
            raise ValueError('Unsupported guest root layout: expected Linux partition 2')
        # qemu-img dd materializes the prefix before extracting boot artifacts.
        # This is a minimum preflight, not a reservation against other writers.
        prefix_bytes = ((start + sectors) * 512 + 1048575) // 1048576 * 1048576
        available = shutil.disk_usage(stage).free
        if available < prefix_bytes:
            raise ValueError('Insufficient temporary disk space for boot refresh: '
                             f'need at least {prefix_bytes} bytes, have {available}; '
                             'guest disk and published boot artifacts are unchanged')
        read_prefix(stage / 'boot.raw', start + sectors)
        with BootPartition(stage / 'boot.raw') as boot:
            boot.extract('config.txt', stage / 'config.txt')
            profile = boot_profile((stage / 'config.txt').read_text())
            boot.extract(profile['kernel'], stage / 'kernel8.img')
            boot.extract(profile['device_tree'], stage / 'cm4-original.dtb')
        validate_kernel(stage / 'kernel8.img')
        try:
            patched = patch_strings((stage / 'cm4-original.dtb').read_bytes(), CM4_PATCH)
        except (ValueError, IndexError, struct.error) as exc:
            raise ValueError(f'Guest device tree is incompatible with the CM4 emulator: {exc}') from exc
        (stage / 'cm4-qemu.dtb').write_bytes(patched)
        config.update({
            'root': f'PARTUUID={struct.unpack_from("<I", mbr, 440)[0]:08x}-02',
            'kernel_sha256': sha256(stage / 'kernel8.img'),
            'dtb_sha256': sha256(stage / 'cm4-qemu.dtb'),
            'boot_profile': profile,
            'boot_config_sha256': sha256(stage / 'config.txt'),
            'original_dtb_sha256': sha256(stage / 'cm4-original.dtb'),
        })
        (stage / 'machine.json').write_text(json.dumps(config, indent=2) + '\n')
        # Validate all files before publication. If interrupted mid-publication,
        # every actual launch refreshes again before constructing its command.
        for name in ('kernel8.img', 'cm4-original.dtb', 'cm4-qemu.dtb', 'machine.json'):
            sync_file(stage / name)
            (stage / name).replace(workspace / name)
        sync_directory(workspace)
    return config
