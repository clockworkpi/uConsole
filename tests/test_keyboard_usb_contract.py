import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from keyboard_usb_contract import (extract_descriptor, inspect, REPORT_SYMBOL,
                                   descriptor_records, extract_usb_templates, USB_TEMPLATE_SYMBOLS)


# Bytes from the locally built, pinned-core ELF; zero-valued runtime patch
# fields intentionally remain zero. These are not a captured configuration.
USB_TEMPLATES = {
    'device': bytes.fromhex('1201000200000140af1e2400000201020001'),
    'configuration_header': bytes.fromhex('09020000000100c032'),
    'hid': bytes.fromhex('0904000001030100000921100100012200000705800340000a'),
    'cdc': bytes.fromhex('080b00020202010209040000010202010005240001100524010301'
                         '042402060524060001070580031000ff09040100020a000000'
                         '0705000240000007058002400000'),
}


def usb_fixture(templates=None):
    templates = USB_TEMPLATES if templates is None else templates
    objects = [(REPORT_SYMBOL, b'\x85\x02')]
    objects += [(USB_TEMPLATE_SYMBOLS[name][0], value) for name, value in templates.items()]
    data = bytearray(4096)
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, b'\x7fELF\x01\x01\x01',
                     2, 40, 1, 0, 0, 52, 0, 52, 0, 0, 40, 4, 0)
    names, payload = bytearray(b'\0'), bytearray()
    for i, (symbol, value) in enumerate(objects):
        struct.pack_into('<IIIBBH', data, 256 + 16 * i, len(names),
                         0x8002000 + len(payload), len(value), 1, 0, 1)
        names += symbol + b'\0'
        payload += value
    struct.pack_into('<IIIIIIIIII', data, 92, 0, 1, 3, 0x8002000, 1024,
                     len(payload), 0, 0, 1, 0)
    struct.pack_into('<IIIIIIIIII', data, 132, 0, 2, 0, 0, 256,
                     len(objects) * 16, 3, 0, 4, 16)
    struct.pack_into('<IIIIIIIIII', data, 172, 0, 3, 0, 0, 512, len(names), 0, 0, 1, 0)
    data[512:512 + len(names)] = names
    data[1024:1024 + len(payload)] = payload
    return data


def fixture():
    data = bytearray(600)
    struct.pack_into('<16sHHIIIIIHHHHHH', data, 0, b'\x7fELF\x01\x01\x01',
                     2, 40, 1, 0, 0, 52, 0, 52, 0, 0, 40, 4, 0)
    descriptor = b'\x85\x02\x75\x08\x95\x06'
    names = b'\0' + REPORT_SYMBOL + b'\0'
    struct.pack_into('<IIIIIIIIII', data, 92, 0, 1, 2, 0x8002000, 512,
                     len(descriptor), 0, 0, 1, 0)
    struct.pack_into('<IIIIIIIIII', data, 132, 0, 2, 0, 0, 256, 16, 3, 0, 4, 16)
    struct.pack_into('<IIIIIIIIII', data, 172, 0, 3, 0, 0, 320, len(names), 0, 0, 1, 0)
    struct.pack_into('<IIIBBH', data, 256, 1, 0x8002000, len(descriptor), 1, 0, 1)
    data[320:320 + len(names)] = names
    data[512:512 + len(descriptor)] = descriptor
    return data, descriptor


class KeyboardUsbContractTests(unittest.TestCase):
    def test_extracts_transport_templates_without_runtime_claims(self):
        data = usb_fixture()
        before = bytes(data)
        usb = extract_usb_templates(data)
        self.assertFalse(usb['runtime_initialized'])
        self.assertFalse(usb['hardware_capture'])
        self.assertTrue(usb['unresolved_runtime_fields'])
        for name, expected in USB_TEMPLATES.items():
            template = usb['templates'][name]
            self.assertEqual(template['hex'], expected.hex())
            self.assertEqual(template['sha256'], hashlib.sha256(expected).hexdigest())
            self.assertEqual(sum(d['length'] for d in template['descriptors']), len(expected))
        self.assertEqual(usb['templates']['hid']['descriptors'][-1]['hex'], '0705800340000a')
        self.assertEqual(bytes(data), before)

    def test_usb_templates_are_opt_in_and_covered_by_elf_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'firmware.elf'
            data = usb_fixture()
            path.write_bytes(data)
            self.assertNotIn('usb', inspect(path))
            result = inspect(path, usb_templates=True)
            self.assertEqual(result['elf_sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(result['usb']['templates']['device']['length'], 18)

    def test_missing_or_wrong_size_transport_template_rejected(self):
        for name in USB_TEMPLATES:
            templates = dict(USB_TEMPLATES)
            del templates[name]
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'exactly one'):
                extract_usb_templates(usb_fixture(templates))
            templates[name] = USB_TEMPLATES[name] + b'\0'
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'template size'):
                extract_usb_templates(usb_fixture(templates))

    def test_invalid_descriptor_boundaries_rejected(self):
        for content in (b'\0', b'\x01\x04', b'\x09\x04', b'\x02\x01\xff', b'\x02\x01\x00'):
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'template'):
                descriptor_records(content)
        templates = dict(USB_TEMPLATES)
        templates['hid'] = b'\0' + templates['hid'][1:]
        with self.assertRaisesRegex(ValueError, 'template'):
            extract_usb_templates(usb_fixture(templates))

    def test_extracts_exact_symbol_and_records_identity_without_modification(self):
        data, descriptor = fixture()
        self.assertEqual(extract_descriptor(data), descriptor)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'firmware.elf'
            path.write_bytes(data)
            result = inspect(path)
            self.assertEqual(result['descriptor_hex'], descriptor.hex())
            self.assertEqual(result['descriptor_sha256'], hashlib.sha256(descriptor).hexdigest())
            self.assertEqual(result['elf_sha256'], hashlib.sha256(data).hexdigest())
            self.assertFalse(result['hardware_capture'])
            self.assertEqual(path.read_bytes(), data)

    def test_truncated_regions_rejected(self):
        data, _ = fixture()
        for length in (0, 6, 51, 200, 260, 330, 515):
            with self.subTest(length=length), self.assertRaises(ValueError):
                extract_descriptor(data[:length])

    def test_wrong_architecture_or_layout_rejected(self):
        for offset, value in ((4, 2), (5, 2), (16, 1), (18, 62), (40, 48), (46, 64), (48, 0)):
            data, _ = fixture()
            data[offset] = value
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                extract_descriptor(data)

    def test_non_file_backed_and_out_of_bounds_symbols_rejected(self):
        mutations = ((96, 8), (100, 0), (260, 0x8001fff), (264, 4097),
                     (264, 7), (132 + 24, 8), (132 + 36, 0), (256, 9999))
        for offset, value in mutations:
            data, _ = fixture()
            struct.pack_into('<I', data, offset, value)
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError):
                extract_descriptor(data)

    def test_missing_and_duplicate_symbols_rejected(self):
        data, _ = fixture()
        data[321] = ord('X')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            extract_descriptor(data)
        data, _ = fixture()
        struct.pack_into('<I', data, 132 + 20, 32)
        data[272:288] = data[256:272]
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            extract_descriptor(data)


if __name__ == '__main__':
    unittest.main()
