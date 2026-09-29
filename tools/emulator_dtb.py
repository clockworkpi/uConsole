"""Small flattened device tree editor for emulator-only string properties."""
import struct


def patch_strings(data, replacements):
    """Return a DTB with specified (absolute node path, property) strings set.

    None deletes a property. Missing properties are added to existing nodes.
    Preserve the reservation map, strings, phandles,
    and all unrelated structure tokens. Fail if the requested nodes drift.
    """
    header = list(struct.unpack_from('>10I', data))
    magic, total, off_struct, off_strings, off_reserve, version, compatible, cpu, strings_size, struct_size = header
    if magic != 0xd00dfeed or version != 17 or total > len(data):
        raise ValueError('Expected a complete version 17 DTB')
    if not (40 <= off_reserve <= off_struct <= off_strings <= total):
        raise ValueError('Unsupported DTB layout')
    strings = bytearray(data[off_strings:off_strings + strings_size])
    source = data[off_struct:off_struct + struct_size]
    output = bytearray()
    stack = []
    found = set()
    pos = 0
    while pos < len(source):
        start = pos
        token, = struct.unpack_from('>I', source, pos)
        pos += 4
        if token == 1:
            end = source.index(0, pos)
            stack.append(source[pos:end].decode('ascii'))
            pos = (end + 4) & ~3
        elif token == 2:
            path = '/'.join(stack) or '/'
            for key, value in replacements.items():
                if key[0] == path and key not in found:
                    if value is not None:
                        name_offset = len(strings)
                        strings.extend(key[1].encode('ascii') + b'\0')
                        value = value.encode('ascii') + b'\0'
                        output.extend(struct.pack('>III', 3, len(value), name_offset))
                        output.extend(value + b'\0' * (-len(value) % 4))
                    found.add(key)
            stack.pop()
        elif token == 3:
            length, name_offset = struct.unpack_from('>II', source, pos)
            name = strings[name_offset:strings.index(0, name_offset)].decode('ascii')
            pos += 8
            key = ('/'.join(stack) or '/', name)
            if key in replacements:
                if replacements[key] is not None:
                    value = replacements[key].encode('ascii') + b'\0'
                    output.extend(struct.pack('>III', 3, len(value), name_offset))
                    output.extend(value + b'\0' * (-len(value) % 4))
                found.add(key)
                pos += (length + 3) & ~3
                continue
            pos += (length + 3) & ~3
        elif token not in (4, 9):
            raise ValueError('Invalid DTB structure token')
        output.extend(source[start:pos])
        if token == 9:
            break
    if found != set(replacements):
        raise ValueError(f'Missing DTB properties: {set(replacements) - found}')
    prefix = data[40:off_struct]
    header[3] = off_struct + len(output)
    header[9] = len(output)
    header[8] = len(strings)
    header[1] = header[3] + len(strings)
    return struct.pack('>10I', *header) + prefix + output + strings
