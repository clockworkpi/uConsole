"""Read the FAT boot partition of an MBR image without mounting it.

Only FAT16/32 and regular files are supported. This deliberately has no write
path: emulator preparation must never edit the source SD image.
"""
import struct


def u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]


def u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


class BootPartition:
    def __init__(self, image):
        self.file = open(image, 'rb')
        try:
            mbr = self.read(0, 512)
            if mbr[510:] != b'\x55\xaa':
                raise ValueError('Not an MBR SD image')
            part = mbr[446:462]
            if part[4] not in (0x06, 0x0e, 0x0b, 0x0c):
                raise ValueError('First partition must be FAT16 or FAT32')
            self.offset = u32(part, 8) * 512
            self.size = u32(part, 12) * 512
            bpb = self.read(self.offset, 512)
            self.sector = u16(bpb, 11)
            self.cluster_size = self.sector * bpb[13]
            if self.sector not in (512, 1024, 2048, 4096) or not bpb[13] or bpb[13] & (bpb[13] - 1):
                raise ValueError('Invalid FAT geometry')
            reserved = u16(bpb, 14)
            fats = bpb[16]
            self.root_entries = u16(bpb, 17)
            fat_sectors = u16(bpb, 22) or u32(bpb, 36)
            self.fat32 = self.root_entries == 0
            self.fat = self.offset + reserved * self.sector
            self.root = self.fat + fats * fat_sectors * self.sector
            root_sectors = (self.root_entries * 32 + self.sector - 1) // self.sector
            self.data = self.root + root_sectors * self.sector
            self.root_cluster = u32(bpb, 44) if self.fat32 else 0
        except Exception:
            self.file.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()

    def read(self, offset, size):
        if offset < 0 or size < 0:
            raise ValueError('Invalid image offset')
        self.file.seek(offset)
        data = self.file.read(size)
        if len(data) != size:
            raise ValueError('Truncated SD image')
        return data

    def chain(self, cluster):
        seen = set()
        limit = 0x0ffffff8 if self.fat32 else 0xfff8
        while cluster < limit:
            if cluster < 2 or cluster in seen:
                raise ValueError('Invalid or cyclic FAT chain')
            seen.add(cluster)
            offset = self.data + (cluster - 2) * self.cluster_size
            if offset + self.cluster_size > self.offset + self.size:
                raise ValueError('FAT cluster outside partition')
            yield self.read(offset, self.cluster_size)
            width = 4 if self.fat32 else 2
            entry = self.read(self.fat + width * cluster, width)
            cluster = int.from_bytes(entry, 'little') & (0x0fffffff if self.fat32 else 0xffff)

    def entries(self, cluster=None):
        if cluster is None and not self.fat32:
            chunks = [self.read(self.root, self.root_entries * 32)]
        else:
            chunks = self.chain(self.root_cluster if cluster is None else cluster)
        long_name = {}
        for chunk in chunks:
            for pos in range(0, len(chunk), 32):
                item = chunk[pos:pos + 32]
                if item[0] == 0:
                    return
                if item[0] == 0xe5:
                    long_name = {}
                    continue
                if item[11] == 0x0f:
                    if item[0] & 0x40:
                        long_name = {}
                    long_name[item[0] & 0x1f] = item[1:11] + item[14:26] + item[28:32]
                    continue
                short = item[:8].decode('ascii', errors='replace').rstrip()
                extension = item[8:11].decode('ascii', errors='replace').rstrip()
                name = short + ('.' + extension if extension else '')
                if long_name:
                    name = b''.join(long_name[k] for k in sorted(long_name)).decode('utf-16le').split('\0')[0].rstrip('\uffff')
                long_name = {}
                if item[11] & 8:
                    continue
                start = u16(item, 26) | (u16(item, 20) << 16 if self.fat32 else 0)
                yield name, start, u32(item, 28), bool(item[11] & 16)

    def extract(self, name, destination):
        parts = name.strip('/').split('/')
        cluster = None
        for index, component in enumerate(parts):
            entry = next((e for e in self.entries(cluster) if e[0].lower() == component.lower()), None)
            if entry is None:
                raise FileNotFoundError(name)
            _, cluster, size, directory = entry
            if index < len(parts) - 1 and not directory:
                raise ValueError('Expected FAT directory')
        if directory:
            raise ValueError('Expected regular boot file')
        with open(destination, 'xb') as output:
            remaining = size
            if remaining:
                for chunk in self.chain(cluster):
                    output.write(chunk[:remaining])
                    remaining -= min(remaining, len(chunk))
                    if not remaining:
                        break
            if remaining:
                raise ValueError('Truncated FAT file')
