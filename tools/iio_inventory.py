#!/usr/bin/env python3
"""Read allowlisted IIO measurements; never scan buses or write device controls."""
import json
from pathlib import Path
import re
import sys


FIELDS = re.compile(r'(?:name|in_(?:voltage|current|temp)[0-9]*_(?:raw|scale|offset))\Z')


def capture_iio(root=Path('/sys/bus/iio/devices')):
    result = {'schema': 1, 'source': 'kernel-iio-sysfs', 'available': False,
              'devices': [], 'errors': []}

    def error(device, field, exc):
        result['errors'].append({'device': device, 'field': field,
                                 'errno': getattr(exc, 'errno', None), 'error': str(exc)})

    def read(path, binary=False):
        with path.open('rb') as stream:
            value = stream.read(65537)
        if len(value) > 65536:
            raise ValueError('attribute exceeds capture limit')
        return value if binary else value.decode().strip()

    try:
        paths = sorted(root.iterdir())
        result['available'] = True
    except FileNotFoundError:
        return result
    except OSError as exc:
        error(None, 'enumeration', exc)
        return result
    for path in paths:
        if not re.fullmatch(r'iio:device[0-9]+', path.name):
            continue
        device = {'name': path.name, 'fields': {}}
        result['devices'].append(device)
        try:
            attributes = sorted(path.iterdir())
        except OSError as exc:
            error(path.name, 'enumeration', exc)
            continue
        for attribute in attributes:
            if FIELDS.fullmatch(attribute.name):
                try:
                    device['fields'][attribute.name] = read(attribute)
                except (OSError, ValueError) as exc:
                    error(path.name, attribute.name, exc)
        for attribute in ('compatible', 'reg'):
            target = path / 'of_node' / attribute
            try:
                value = read(target, binary=True)
                device[attribute] = (value.decode().rstrip('\0').split('\0')
                                     if attribute == 'compatible' else value.hex())
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                error(path.name, attribute, exc)
        for link in (path / 'device/driver', path / 'device/device/driver',
                     path.resolve().parent / 'driver'):
            if link.is_symlink():
                device['driver'] = link.resolve().name
                break
    return result


def contract(snapshot):
    if (not isinstance(snapshot, dict) or type(snapshot.get('schema')) is not int
            or snapshot['schema'] != 1 or snapshot.get('source') != 'kernel-iio-sysfs'
            or type(snapshot.get('available')) is not bool
            or not isinstance(snapshot.get('devices'), list)
            or not isinstance(snapshot.get('errors'), list)
            or (not snapshot['available'] and snapshot['devices'])):
        raise ValueError('Invalid IIO inventory')
    devices = []
    for item in snapshot['devices']:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not isinstance(item.get('fields'), dict):
            raise ValueError('Invalid IIO device')
        fields = dict(item['fields'])
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in fields.items()):
            raise ValueError('Invalid IIO fields')
        if 'name' in fields:
            fields['name'] = re.sub(r'^\d+-([0-9a-f]{4})$', r'i2c@\1', fields['name'])
        normalized = {'fields': fields}
        for key in ('compatible', 'reg', 'driver'):
            if key in item:
                value = item[key]
                if ((key == 'compatible' and (not isinstance(value, list)
                                             or any(not isinstance(v, str) for v in value)))
                        or (key != 'compatible' and not isinstance(value, str))):
                    raise ValueError('Invalid IIO identity')
                normalized[key] = value
        devices.append(normalized)
    return {'available': snapshot['available'],
            'devices': sorted(devices, key=lambda value: json.dumps(value, sort_keys=True))}


def compare_iio(reference, candidate):
    left, right = contract(reference), contract(candidate)
    complete = not reference['errors'] and not candidate['errors']
    return {'scope': 'IIO identity and sampled attributes; not electrical or timing equivalence',
            'complete': complete, 'matching': complete and left == right,
            'reference': left, 'candidate': right,
            'reference_errors': reference['errors'], 'candidate_errors': candidate['errors']}


if __name__ == '__main__':
    observed = capture_iio()
    print(json.dumps(observed))
    sys.exit(2 if observed['errors'] else 0)
