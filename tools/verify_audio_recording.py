"""Check the validator's known stereo 440 Hz tone in a finalized QEMU WAV."""
from array import array
import math
import sys
import wave


def verify(path, repetitions=1):
    if type(repetitions) is not int or not 1 <= repetitions <= 11:
        raise ValueError('Repetitions must be an integer from 1 to 11')
    with wave.open(str(path), 'rb') as recording:
        if (recording.getnchannels(), recording.getsampwidth(), recording.getframerate()) != (2, 2, 48000):
            raise ValueError('Unexpected WAV format; expected stereo S16_LE at 48 kHz')
        frames = recording.getnframes()
        if not 48000 <= frames <= 48000 * 120:
            raise ValueError('Recording duration outside qualification bounds')
        data = recording.readframes(frames)
    if len(data) != frames * 4:
        raise ValueError('Truncated WAV sample data')
    samples = array('h', data)
    if sys.byteorder != 'little':
        samples.byteswap()
    left, right = samples[::2], samples[1::2]
    if left != right:
        raise ValueError('Stereo channels differ from the generated identical-channel tone')
    active = [index for index, sample in enumerate(left) if abs(sample) > 100]
    if len(active) < 40000:
        raise ValueError('Recording lacks one second of non-silent test tone')
    first, last = active[0], active[-1]
    # Check one contiguous tone, excluding leading/trailing backend silence.
    segment = left[first:last + 1]
    crossings = sum(a <= 0 < b for a, b in zip(segment, segment[1:]))
    frequency = crossings * 48000 / len(segment)
    if repetitions == 1 and not 435 <= frequency <= 445:
        raise ValueError('Recorded tone frequency differs from 440 Hz: ' + str(frequency))
    # Total WAV length includes backend silence. It cannot establish that the
    # entire input tone survived. Match all 48,000 generated samples, allowing
    # a constant mixer gain and integer quantization, but no missing tail,
    # inserted silence or discontinuous samples inside that window.
    reference = [int(4096 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000)]
    energy = sum(x * x for x in reference)
    matches = []
    cursor = 0
    for _ in range(repetitions):
        first = next((i for i in range(cursor, len(left)) if abs(left[i]) > 100), len(left))
        match = None
        for start in range(max(cursor, first - 32), first + 1):
            if start + 48000 > len(left):
                continue
            window = left[start:start + 48000]
            gain = sum(x * y for x, y in zip(reference, window)) / energy
            if not 0.05 <= gain <= 2:
                continue
            error = max(abs(y - gain * x) for x, y in zip(reference, window))
            if error <= 2:
                match = {'start_frame': start, 'matched_frames': 48000,
                         'mixer_gain': gain, 'maximum_sample_error': error}
                break
        if match is None:
            raise ValueError('Recording does not contain the complete uninterrupted 48,000-frame input tone')
        matches.append(match)
        cursor = match['start_frame'] + 48000
    if any(abs(x) > 100 for x in left[cursor:]):
        raise ValueError('Unexpected audio after the requested input tones')
    return {'status': 'verified', 'frames': frames, 'active_frames': len(active),
            'rate': 48000, 'channels': 2, 'peak': max(abs(x) for x in left),
            'measured_frequency_hz': frequency if repetitions == 1 else None,
            'waveform': matches[0], 'waveforms': matches,
            'repetitions': repetitions, 'path': str(path)}
