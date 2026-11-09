#!/usr/bin/env python3
"""Extract the keyboard HID descriptor and optional compiled USB templates.

This is a source-build contract, not a USB capture or MCU execution result.
USB templates precede runtime endpoint/interface allocation and identity setup.
Only unstripped, little-endian ARM ELF32 executables are supported.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct


MAX_ELF = 32 * 1024 * 1024
REPORT_SYMBOL = b'_ZL17reportDescription'
USB_TEMPLATE_SYMBOLS = {
    'device': (b'usbGenericDescriptor_Device', 18),
    'configuration_header': (b'Base_Header', 9),
    'hid': (b'hidPartConfigData', 25),
    'cdc': (b'serialPartConfigData', 66),
}


def extract_object(data, symbol):
    def region(offset, size):
        if offset < 0 or size < 0 or offset + size > len(data):
            raise ValueError('Truncated ELF region')
        return data[offset:offset + size]

    if len(data) > MAX_ELF or region(0, 7) != b'\x7fELF\x01\x01\x01':
        raise ValueError('Expected little-endian ELF32 firmware')
    header = struct.unpack('<16sHHIIIIIHHHHHH', region(0, 52))
    if header[1:4] != (2, 40, 1) or header[8] != 52 or header[11] != 40:
        raise ValueError('Expected ARM executable with standard ELF32 headers')
    section_offset, section_count = header[6], header[12]
    if not section_count:
        raise ValueError('Missing ELF section table')
    table = region(section_offset, section_count * 40)
    sections = list(struct.iter_unpack('<IIIIIIIIII', table))
    matches = []
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        if section[9] != 16 or section[5] % 16 or section[6] >= section_count:
            raise ValueError('Invalid ELF symbol table')
        strings = sections[section[6]]
        if strings[1] != 3:
            raise ValueError('Invalid ELF symbol string table')
        names = region(strings[4], strings[5])
        for name, address, size, info, _, index in struct.iter_unpack(
                '<IIIBBH', region(section[4], section[5])):
            end = names.find(b'\0', name, name + 4096)
            if name >= len(names) or end < 0:
                raise ValueError('Invalid ELF symbol name')
            if names[name:end] != symbol:
                continue
            if info & 15 != 1 or not 0 < index < section_count or not 0 < size <= 4096:
                raise ValueError('Invalid descriptor object symbol')
            owner = sections[index]
            relative = address - owner[3]
            if owner[1] != 1 or not owner[2] & 2 or relative < 0 or relative + size > owner[5]:
                raise ValueError('Descriptor object is not backed by an allocated data section')
            matches.append(region(owner[4] + relative, size))
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one compiled {symbol.decode()} symbol; use unstripped firmware')
    return matches[0]


def extract_descriptor(data):
    return extract_object(data, REPORT_SYMBOL)


def descriptor_records(data):
    """Walk a USB descriptor template without guessing runtime substitutions."""
    records = []
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length < 2 or offset + length > len(data):
            raise ValueError('Invalid or truncated USB descriptor template')
        part = data[offset:offset + length]
        records.append({'offset': offset, 'length': length, 'type': part[1], 'hex': part.hex()})
        offset += length
    return records


def extract_usb_templates(data):
    templates = {}
    for name, (symbol, length) in USB_TEMPLATE_SYMBOLS.items():
        content = extract_object(data, symbol)
        if len(content) != length:
            raise ValueError(f'Unsupported {name} template size: {len(content)}')
        templates[name] = {
            'symbol': symbol.decode(), 'length': len(content),
            'sha256': hashlib.sha256(content).hexdigest(), 'hex': content.hex(),
            'descriptors': descriptor_records(content),
        }
    return {'evidence_kind': 'compiled-firmware-usb-templates',
            'runtime_initialized': False, 'hardware_capture': False,
            'templates': templates,
            'unresolved_runtime_fields': [
                'interface numbering and endpoint allocation',
                'HID report descriptor length and endpoint packet sizes',
                'configuration total length and interface count',
                'device identity overrides and string descriptors',
            ]}


def inspect(path, *, usb_templates=False):
    with Path(path).open('rb') as source:
        data = source.read(MAX_ELF + 1)
    descriptor = extract_descriptor(data)
    result = {'schema': 1, 'evidence_kind': 'compiled-firmware-hid-descriptor',
            'hardware_capture': False, 'elf_sha256': hashlib.sha256(data).hexdigest(),
            'symbol': REPORT_SYMBOL.decode(), 'descriptor_length': len(descriptor),
            'descriptor_sha256': hashlib.sha256(descriptor).hexdigest(),
            'descriptor_hex': descriptor.hex()}
    if usb_templates:
        result['usb'] = extract_usb_templates(data)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('elf', type=Path)
    cli.add_argument('--output', type=Path, help='create a new JSON evidence file; never overwrite')
    cli.add_argument('--usb-templates', action='store_true',
                     help='also extract pre-initialization USB templates, not runtime descriptors')
    args = cli.parse_args()
    document = json.dumps(inspect(args.elf, usb_templates=args.usb_templates), indent=2) + '\n'
    if args.output:
        with args.output.open('x') as output:
            output.write(document)
    else:
        print(document, end='')


if __name__ == '__main__':
    main()
