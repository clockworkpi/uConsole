"""Bounded observation recordings and deterministic, non-actuating playback."""
from datetime import datetime, timezone
from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import stat

from workbench_schematic import Observations

SCHEMA = 'uconsole-schematic-observations-v1'
MAX_EVENTS = 10000
MAX_BYTES = 16 * 1024 * 1024


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate trace key: ' + key)
        value[key] = item
    return value


def validate(data):
    if not isinstance(data, dict) or set(data) != {'schema', 'identity', 'created', 'events', 'dropped'}:
        raise ValueError('Invalid observation recording header')
    if data['schema'] != SCHEMA:
        raise ValueError('Unsupported observation recording version')
    identity = data['identity']
    if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
        raise ValueError('Invalid recorded runtime identity')
    if not isinstance(data['created'], str) or len(data['created']) > 128:
        raise ValueError('Invalid recording date')
    if type(data['dropped']) is not int or data['dropped'] < 0:
        raise ValueError('Invalid dropped observation count')
    events = data['events']
    if not isinstance(events, list) or not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('Recording must contain 1..10000 observations')
    state = Observations()
    state.bind(identity)
    last = 0
    for event in events:
        if not isinstance(event, dict) or set(event) != {'identity', 'component', 'state', 'time', 'source', 'detail'}:
            raise ValueError('Invalid recorded observation fields')
        if not state.accept(event) or event['time'] < last:
            raise ValueError('Recording identities or times are inconsistent')
        last = event['time']
        if last > 24 * 60 * 60:
            raise ValueError('Recording exceeds 24 hours')
    return data


class Recording:
    def __init__(self, identity, start):
        self.start = start
        self.data = dict(schema=SCHEMA, identity=identity,
                         created=datetime.now(timezone.utc).isoformat(), events=[], dropped=0)
        self.full = False
        self.bytes = 0

    def append(self, event):
        if event['identity'] != self.data['identity'] or event['time'] < self.start:
            return False
        if len(self.data['events']) >= MAX_EVENTS or event['time'] - self.start > 24 * 60 * 60:
            self.full = True
            self.data['dropped'] += 1
            return False
        copied = dict(event, time=event['time'] - self.start)
        size = len(json.dumps(copied, ensure_ascii=True, separators=(',', ':')).encode('utf-8')) + 1
        if self.bytes + size > MAX_BYTES - 4096:
            self.full = True
            self.data['dropped'] += 1
            return False
        self.bytes += size
        self.data['events'].append(copied)
        return True

    def save(self, path):
        data = json.dumps(validate(self.data), ensure_ascii=True, separators=(',', ':')).encode('utf-8') + b'\n'
        if len(data) > MAX_BYTES:
            raise ValueError('Observation recording exceeds file size limit')
        # Never overwrite an existing trace or follow a symlink chosen by mistake.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)


class Playback:
    def __init__(self, data):
        self.data = validate(data)
        self.duration = self.data['events'][-1]['time']
        self.index = {}
        for event in self.data['events']:
            times, events = self.index.setdefault(event['component'], ([], []))
            times.append(event['time'])
            events.append(event)

    @classmethod
    def load(cls, path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError('Recording must be a regular file within the size limit')
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError('Observation recording exceeds file size limit')
        return cls(json.loads(content, object_pairs_hook=unique))

    def seek(self, position):
        if type(position) not in (int, float) or not math.isfinite(position) or position < 0:
            raise ValueError('Invalid playback time')
        state = Observations()
        state.bind(self.data['identity'])
        state.dropped = self.data['dropped']
        for times, events in self.index.values():
            index = bisect_right(times, position) - 1
            if index >= 0:
                state.accept(events[index])
        return state
