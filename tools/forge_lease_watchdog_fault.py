"""Failure injection source for an explicitly disposable QEMU recovery guest.

Never execute this on a physical target. The guest command line must identify
emulation; all three processes must belong to the expected lease launcher.
"""

SOURCE = r'''
import json,os,signal,time
from pathlib import Path
tokens=Path('/proc/cmdline').read_text().split()
for flag in ('uconsole.emulator=1','uconsole.recovery=1','uconsole.recovery_lease=1'):
    if tokens.count(flag)!=1: raise ValueError('Not an explicit disposable emulator lease')
ready=json.loads(Path('/run/forge-watchdog.ready').read_text())
keeper=ready['pid']
if type(keeper) is not int or keeper<=1 or ready.get('lease') is not True:
    raise ValueError('Invalid keeper identity')
def stat(pid):
    fields=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()
    return fields[0],int(fields[1])
_,parent=stat(keeper)
if parent<=1: raise ValueError('Missing lease supervisor')
children=list(map(int,Path(f'/proc/{parent}/task/{parent}/children').read_text().split()))
if len(children)!=2 or keeper not in children: raise ValueError('Unexpected timer process tree')
software=next(pid for pid in children if pid!=keeper)
expected=[b'/usr/bin/python3',b'-I',b'-S',b'/etc/forge/lease-launch.py']
for pid in [parent,*children]:
    if Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(bytes([0])).split(bytes([0]))!=expected:
        raise ValueError('Process is not the disposable lease launcher')
    if Path(f'/proc/{pid}/exe').resolve()!=Path('/usr/bin/python3').resolve():
        raise ValueError('Unexpected timer executable')
if Path('/sys/class/watchdog/watchdog0/state').read_text().strip()!='active':
    raise ValueError('Watchdog not armed')
if Path('/sys/class/watchdog/watchdog0/timeout').read_text().strip()!='15':
    raise ValueError('Watchdog timeout differs')
def stop(pid):
    os.kill(pid,signal.SIGSTOP)
    until=time.monotonic()+2
    while stat(pid)[0]!='T':
        if time.monotonic()>=until: raise TimeoutError('Process did not stop')
        time.sleep(.01)
stop(parent)
stop(software)
os.kill(keeper,signal.SIGKILL)
print(json.dumps(dict(supervisor=parent,software=software,keeper=keeper,
                     supervisor_stopped=True,software_stopped=True,keeper_killed=True)),flush=True)
'''
