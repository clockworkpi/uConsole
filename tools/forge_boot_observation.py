"""Read-only physical boot evidence, independent of SSH disconnect success."""
import json
import re
import shlex
import subprocess
from pathlib import Path


def read_boot():
    """Read local physical boot identity without invoking a subprocess."""
    chosen = Path('/proc/device-tree/chosen/bootloader')
    def cell(name):
        value = (chosen/name).read_bytes()
        if len(value) != 4:
            raise ValueError('Expected one device-tree cell')
        return int.from_bytes(value, 'big')
    return dict(machine_id=Path('/etc/machine-id').read_text().strip(),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                cmdline=Path('/proc/cmdline').read_text().strip(),
                tryboot=cell('tryboot'), partition=cell('partition'))


def normal_boot(value, machine):
    fields = {'machine_id', 'boot_id', 'cmdline', 'tryboot', 'partition'}
    if (not isinstance(value, dict) or set(value) != fields or value['machine_id'] != machine or
            not isinstance(value['boot_id'], str) or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value['boot_id']) or
            type(value['tryboot']) is not int or value['tryboot'] != 0 or
            type(value['partition']) is not int or value['partition'] != 1 or not isinstance(value['cmdline'], str)):
        raise ValueError('Expected the publication target on its normal physical boot')
    tokens = value['cmdline'].split()
    roots = [token for token in tokens if token.startswith('root=')]
    if (len(roots) != 1 or roots[0] in ('root=', 'root=/dev/ram0') or
            any(token.startswith(('uconsole.forge_trial=', 'uconsole.recovery', 'uconsole.emulator=')) for token in tokens)):
        raise ValueError('Normal boot contains a recovery/emulator/trial root or marker')
    return value

READER = '''import json
from pathlib import Path
p = Path('/proc/device-tree/chosen/bootloader')
def cell(name):
    data = (p / name).read_bytes()
    if len(data) != 4:
        raise ValueError('Expected one device-tree cell')
    return int.from_bytes(data, 'big')
print(json.dumps(dict(machine_id=Path('/etc/machine-id').read_text().strip(),
    boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    cmdline=Path('/proc/cmdline').read_text().strip(),
    tryboot=cell('tryboot'), partition=cell('partition'))))
'''


def capture(host):
    if not isinstance(host, str) or not re.fullmatch(
            r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Invalid SSH host')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
                             host, 'python3 -c ' + shlex.quote(READER)],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=True)
    return json.loads(result.stdout)


def verify(observed, previous, machine_id, nonce=None):
    if observed.get('machine_id') != machine_id or previous.get('machine_id') != machine_id:
        raise ValueError('Observed another machine')
    for item in (observed, previous):
        if not isinstance(item.get('boot_id'), str) or not re.fullmatch(
                r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', item['boot_id']):
            raise ValueError('Invalid boot identity')
    if observed['boot_id'] == previous['boot_id']:
        raise ValueError('Still observing the previous boot')
    if type(observed.get('partition')) is not int or observed['partition'] != previous.get('partition'):
        raise ValueError('Boot partition changed')
    if not isinstance(observed.get('cmdline'), str):
        raise ValueError('Missing command line')
    tokens = observed['cmdline'].split()
    markers = [t for t in tokens if t.startswith('uconsole.forge_trial=')]
    if any(t.startswith('uconsole.emulator=') for t in tokens):
        raise ValueError('Physical boot enabled emulator adapter')
    if nonce is not None and (not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce)):
        raise ValueError('Invalid expected trial nonce')
    if type(observed.get('tryboot')) is not int or observed['tryboot'] != int(nonce is not None):
        raise ValueError('Unexpected firmware tryboot flag')
    if markers != ([] if nonce is None else ['uconsole.forge_trial=' + nonce]):
        raise ValueError('Trial marker mismatch')
    roots = lambda text: [t for t in text.split() if t.startswith('root=')]
    if len(roots(observed['cmdline'])) != 1 or roots(observed['cmdline']) != roots(previous['cmdline']):
        raise ValueError('Native root changed')
    return {'status': 'verified-trial' if nonce else 'verified-normal',
            'boot_id': observed['boot_id'], 'recovery_qualified': False}
