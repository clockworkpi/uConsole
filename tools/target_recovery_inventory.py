"""Read-only boot recovery prerequisites; never a backup or readiness claim."""
import base64
import hashlib
import json
from pathlib import Path


PATHS = (
    '/etc/machine-id', '/proc/cmdline', '/proc/self/mountinfo',
    '/boot/firmware/config.txt', '/boot/firmware/cmdline.txt',
    '/boot/firmware/tryboot.txt', '/boot/firmware/autoboot.txt',
    '/proc/device-tree/chosen/bootloader/version',
    '/proc/device-tree/chosen/bootloader/tryboot',
    '/proc/device-tree/chosen/bootloader/partition',
    '/sys/class/watchdog/watchdog0/identity',
    '/sys/class/watchdog/watchdog0/state',
    '/sys/class/watchdog/watchdog0/timeout',
    '/sys/class/watchdog/watchdog0/nowayout',
    '/sys/class/watchdog/watchdog0/bootstatus',
    '/sys/module/watchdog/parameters/open_timeout',
    '/proc/device-tree/soc/watchdog@7e100000/early-watchdog',
)
LIMIT = 1024 * 1024


def capture(root=Path('/')):
    result = {'schema': 1, 'source': 'read-only-recovery-inventory',
              'recovery_qualified': False, 'files': {}, 'errors': []}
    for name in PATHS:
        path = root / name.lstrip('/')
        try:
            with path.open('rb') as stream:
                data = stream.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError('capture limit exceeded')
            result['files'][name] = {
                'status': 'read', 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
                'base64': base64.b64encode(data).decode('ascii')}
        except FileNotFoundError:
            result['files'][name] = {'status': 'absent'}
        except (OSError, ValueError) as exc:
            result['files'][name] = {'status': 'error'}
            result['errors'].append({'path': name, 'error': str(exc)})
    def text(path):
        entry = result['files'].get(path, {})
        if entry.get('status') != 'read':
            return None
        try:
            return base64.b64decode(entry['base64']).decode('ascii').strip()
        except (ValueError, UnicodeDecodeError):
            return None
    # Report observations separately. An active runtime watchdog or a nonzero
    # parameter cannot establish firmware arming, ownership, or reset behavior.
    result['watchdog_observations'] = {
        'runtime_state': text('/sys/class/watchdog/watchdog0/state'),
        'runtime_timeout': text('/sys/class/watchdog/watchdog0/timeout'),
        'kernel_open_timeout': text('/sys/module/watchdog/parameters/open_timeout'),
        'bootstatus': text('/sys/class/watchdog/watchdog0/bootstatus'),
        'firmware_handoff_qualified': False,
        'failed_boot_fallback_qualified': False,
    }
    return result


if __name__ == '__main__':
    result = capture()
    print(json.dumps(result))
    raise SystemExit(2 if result['errors'] else 0)
