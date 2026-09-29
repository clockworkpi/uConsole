"""Owned USB playback/synthetic-capture inspection and bounded hotplug."""
import json
import os
import threading
import time

DEVICE = 'audio-surrogate'
CAPTURE_DEVICE = 'audio-capture'
MODES = ('none', 'usb-null', 'usb-wav', 'usb-capture', 'usb-duplex')
RECORDING_MODES = ('usb-wav', 'usb-duplex')


def devices(model):
    if model == 'usb-capture':
        return [{'driver': 'usb-forge-capture', 'id': DEVICE}]
    if model in ('usb-null', 'usb-wav', 'usb-duplex'):
        result = [{'driver': 'usb-audio', 'id': DEVICE, 'audiodev': 'forge-audio'}]
        if model == 'usb-duplex':
            result.append({'driver': 'usb-forge-capture', 'id': CAPTURE_DEVICE})
        return result
    raise ValueError('Boot this owned VM with a USB audio mode first')


def query(runtime):
    model = getattr(runtime.args, 'audio', 'none')
    expected = devices(model)
    capture = model in ('usb-capture', 'usb-duplex')
    children = runtime.control('qom-list', {'path': '/machine/peripheral'})
    if not isinstance(children, list) or any(not isinstance(item, dict) for item in children):
        raise ValueError('Invalid QOM audio inventory')
    states = {}
    for device in expected:
        matches = [item for item in children if item.get('name') == device['id']]
        if len(matches) > 1 or (matches and matches[0].get('type') != 'child<' + device['driver'] + '>'):
            raise ValueError('Audio identity belongs to an unexpected QOM device')
        attached = False
        if matches:
            attached = runtime.control('qom-get', {'path': '/machine/peripheral/' + device['id'], 'property': 'attached'})
            if type(attached) is not bool:
                raise ValueError('Invalid audio attachment readback')
        states[device['id']] = {'present': bool(matches), 'connected': attached}
    return {'model': model, 'present': all(item['present'] for item in states.values()),
            'connected': all(item['connected'] for item in states.values()), 'devices': states,
            'playback': model != 'usb-capture',
            'capture': capture, 'host_microphone': False,
            'coverage': ('Two USB surrogates: WAV playback and synthetic capture; not native duplex audio' if model == 'usb-duplex' else
                         'Synthetic USB capture fixture; not native audio or microphone' if capture else
                         'USB playback surrogate; not native audio or jack state')}


def operation(runtime, evidence, connected=None):
    if connected is not None and type(connected) is not bool:
        raise ValueError('Audio connected state must be boolean')
    with evidence.open('x') as stream:
        def record(value):
            stream.write(json.dumps(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        record({'runtime_identity': runtime.identity, 'operation': 'query' if connected is None else 'set',
                'requested': connected})
        parent = os.open(evidence.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        try:
            before = query(runtime)
            record({'before': before})
            if connected is None:
                result = before
            else:
                # A present but detached object is not safe to silently replace.
                if any(item['present'] != item['connected'] for item in before['devices'].values()):
                    raise ValueError('Audio device is transitioning; inspect before retrying')
                for properties in devices(before['model']):
                    if before['devices'][properties['id']]['connected'] != connected:
                        command = 'device_add' if connected else 'device_del'
                        record({'device': properties['id'], 'command': command, 'state': 'dispatch'})
                        if connected:
                            runtime.control('device_add', properties)
                        else:
                            runtime.control('device_del', {'id': properties['id']})
                        record({'device': properties['id'], 'command': command, 'state': 'acknowledged'})
                deadline = time.monotonic() + 5
                while True:
                    observed = query(runtime)
                    if all(item['present'] == connected and item['connected'] == connected
                           for item in observed['devices'].values()):
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Audio hotplug readback deadline; effect may be incomplete')
                    threading.Event().wait(0.05)
                result = {'before': before, 'observed': observed}
            record({'status': 'completed', 'result': result})
            return result
        except BaseException as exc:
            record({'status': 'failed', 'error': str(exc), 'rollback': False})
            raise
