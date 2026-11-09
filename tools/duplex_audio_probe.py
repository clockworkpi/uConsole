"""Guest-side concurrent ALSA probe; never opens host audio devices."""
import inspect

from capture_pattern import verify_capture


def verified_evidence(payload):
    if not isinstance(payload, dict):
        return False
    record = payload.get('duplex')
    if not isinstance(record, dict):
        return False
    capture, overlap = record.get('capture', {}), record.get('overlap', {})
    return (isinstance(capture, dict) and isinstance(overlap, dict) and
            record.get('playback_frames') == 48000 and record.get('capture_frames') == 144000 and
            capture.get('frames') == 144000 and capture.get('pattern') == 'stereo-u32-frame-counter-v1' and
            record.get('playback_card') != record.get('capture_card') and
            all(isinstance(overlap.get(key), str) and 'state: RUNNING' in overlap[key]
                for key in ('playback', 'capture')))


def probe():
    import json
    import math
    import pathlib
    import struct
    import subprocess
    import tempfile
    import time
    import wave

    def card(usb_id):
        matches = [p.parent for p in pathlib.Path('/proc/asound').glob('card*/usbid')
                   if p.read_text().strip() == usb_id]
        if len(matches) != 1:
            raise RuntimeError('Expected one USB card: ' + usb_id)
        path = matches[0]
        if (pathlib.Path('/sys/class/sound') / path.name / 'device/driver').resolve().name != 'snd-usb-audio':
            raise RuntimeError('Expected production USB audio driver')
        return path, 'hw:' + path.name.removeprefix('card') + ',0'

    playback_card, playback_device = card('46f4:0002')
    capture_card, capture_device = card('46f4:0003')
    if playback_card == capture_card:
        raise RuntimeError('Expected two explicitly separate surrogate cards')

    def state(path, direction):
        return (path / ('pcm0' + direction) / 'sub0/status').read_text()

    with tempfile.TemporaryDirectory(prefix='forge-duplex-') as directory:
        directory = pathlib.Path(directory)
        source, captured = directory / 'tone.wav', directory / 'capture.raw'
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b''.join(struct.pack('<hh', value, value) for value in
                (int(4096 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(48000))))
        children = []
        with tempfile.TemporaryFile() as capture_log, tempfile.TemporaryFile() as playback_log:
            try:
                capture = subprocess.Popen(['arecord', '-D', capture_device, '-t', 'raw',
                    '-f', 'S16_LE', '-r', '48000', '-c', '2', '-d', '3', str(captured)],
                    stdout=subprocess.DEVNULL, stderr=capture_log)
                children.append(capture)
                deadline = time.monotonic() + 10
                while 'state: RUNNING' not in state(capture_card, 'c'):
                    if capture.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Capture did not start before playback')
                    time.sleep(0.005)
                playback = subprocess.Popen(['aplay', '-D', playback_device, str(source)],
                                             stdout=subprocess.DEVNULL, stderr=playback_log)
                children.append(playback)
                overlap = None
                deadline = time.monotonic() + 20
                while playback.poll() is None:
                    play_state, capture_state = state(playback_card, 'p'), state(capture_card, 'c')
                    if 'state: RUNNING' in play_state and 'state: RUNNING' in capture_state:
                        overlap = {'playback': play_state, 'capture': capture_state}
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Concurrent playback deadline')
                    time.sleep(0.005)
                if playback.returncode or capture.wait(timeout=10):
                    raise RuntimeError('Concurrent ALSA client failed')
                if overlap is None:
                    raise RuntimeError('No simultaneous RUNNING playback/capture observed')
                checked = verify_capture(captured.read_bytes(), frames=144000)
                capture_log.seek(0)
                playback_log.seek(0)
                print(json.dumps({'status': 'played', 'duplex': {
                    'capture': checked, 'overlap': overlap,
                    'playback_card': playback_card.name, 'capture_card': capture_card.name,
                    'playback_frames': 48000, 'capture_frames': 144000,
                    'capture_log': capture_log.read().decode(), 'playback_log': playback_log.read().decode(),
                    'scope': 'two concurrent USB surrogate cards; not native duplex hardware'}}))
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                    child.wait()


def source():
    return inspect.getsource(verify_capture) + '\n' + inspect.getsource(probe) + '\nprobe()\n'
