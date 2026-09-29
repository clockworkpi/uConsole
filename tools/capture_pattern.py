"""Sample-level contract for the synthetic capture sequence, not analog audio."""


def verify_capture(payload, frames=48000):
    import struct
    if type(frames) is not int or not 1 <= frames <= 48000 * 120:
        raise ValueError('Capture frame count outside qualification bounds')
    if len(payload) != frames * 4:
        raise ValueError('Wrong capture byte length')
    # Each stereo S16_LE frame carries a little-endian uint32 counter. Its
    # halves are deliberately different; no gain/resampling is acceptable.
    initial = struct.unpack_from('<I', payload)[0]
    for index, (value,) in enumerate(struct.iter_unpack('<I', payload)):
        if value != (initial + index) & 0xffffffff:
            raise ValueError('Capture sequence discontinuity at frame ' + str(index))
    return {'frames': frames, 'initial_sequence': initial,
            'last_sequence': (initial + frames - 1) & 0xffffffff,
            'pattern': 'stereo-u32-frame-counter-v1'}
