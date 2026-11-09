"""Bounded host-clock power-event replay with explicit partial-effect evidence."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time

from forge_scenario import change_power, unique_object, validate_value


class ReplayCancelled(Exception):
    """Future events stopped; earlier events are not rolled back."""


def completed_event_records(records):
    """Select event outcomes, not per-event intent/ack/readback or run summaries."""
    return [record for record in records
            if 'event' in record and record.get('status') == 'completed']


def run_recorded(replay, runtime, cancel, evidence):
    """Shared GUI/controller worker; never touches UI state."""
    with Path(evidence).open('x') as record:
        def emit(value):
            record.write(json.dumps(value) + '\n')
            record.flush()
            os.fsync(record.fileno())
        emit({'runtime_identity': runtime.identity, 'source_sha256': replay.sha256,
              'clock': 'host-monotonic'})
        parent = os.open(Path(evidence).parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        result = replay.run(runtime.control, cancel, emit)
    return dict(result, evidence_path=str(evidence))


@dataclass(frozen=True)
class Event:
    at_ms: int
    field: str
    value: object


@dataclass(frozen=True)
class Replay:
    sha256: str
    events: tuple

    @classmethod
    def load(cls, path):
        with Path(path).open('rb') as source:
            payload = source.read(16385)
        if len(payload) > 16384:
            raise ValueError('Replay exceeds 16 KiB')
        data = json.loads(payload, object_pairs_hook=unique_object)
        if not isinstance(data, dict) or set(data) != {'schema', 'events'}:
            raise ValueError('Replay requires exactly schema and events')
        if type(data['schema']) is not int or data['schema'] != 1:
            raise ValueError('Unsupported replay schema')
        entries = data['events']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
            raise ValueError('Replay requires 1 to 64 events')
        events = []
        previous = -1
        for item in entries:
            if not isinstance(item, dict) or set(item) != {'at_ms', 'power'}:
                raise ValueError('Event requires exactly at_ms and power')
            at = item['at_ms']
            if type(at) is not int or not 0 <= at <= 300000 or at < previous:
                raise ValueError('Event times must be ordered integers from 0 to 300000 ms')
            changes = item['power']
            if not isinstance(changes, dict) or len(changes) != 1:
                raise ValueError('Each event changes exactly one power field')
            field, value = next(iter(changes.items()))
            validate_value(field, value)
            events.append(Event(at, field, value))
            previous = at
        return cls(hashlib.sha256(payload).hexdigest(), tuple(events))

    def run(self, control, cancel, emit, *, clock=time.monotonic):
        """Sequential dispatch; timestamps are host elapsed time, not guest time."""
        start = clock()
        completed = 0
        try:
            for index, event in enumerate(self.events):
                if cancel.wait(max(0, event.at_ms / 1000 - (clock() - start))):
                    raise ReplayCancelled('Replay cancelled; completed events are not rolled back')
                if not control('query-status')['running']:
                    raise ValueError('Replay requires a running owned VM; it will not resume one')
                if cancel.is_set():
                    raise ReplayCancelled('Replay cancelled; completed events are not rolled back')
                emit({'event': index, 'status': 'dispatching', 'scheduled_ms': event.at_ms,
                      'elapsed_ms': (clock() - start) * 1000,
                      'requested': {event.field: event.value}})
                def record_change(value):
                    emit({'event': index, 'elapsed_ms': (clock() - start) * 1000, **value})
                result = change_power(control, event.field, event.value, record=record_change)
                completed += 1
                emit({'event': index, 'status': 'completed', 'elapsed_ms': (clock() - start) * 1000,
                      'result': result})
            result = {'completed_events': completed, 'source_sha256': self.sha256,
                      'clock': 'host-monotonic', 'elapsed_ms': (clock() - start) * 1000}
            emit({'status': 'completed', **result})
            return result
        except BaseException as exc:
            emit({'status': 'cancelled' if isinstance(exc, ReplayCancelled) else 'failed',
                  'completed_events': completed, 'error': str(exc), 'rollback': False})
            raise
