"""Disposable QEMU reboot and stale-worker qualification, never physical reboot."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from forge_recovery_commit_protocol import Protocol
from forge_recovery_commit_transport import exchange
from forge_recovery_commit_worker import BOOTSTRAP, payload
from forge_recovery_bootcommit import validate
from forge_recovery_lease_client import LeasePulse


def restart(process,command,ssh,probe,boot_id,output,console_path):
    if probe.mode!='emulated' or process.poll() is not None:
        raise ValueError('Reboot qualification requires this live emulator process')
    expected='if=sd,format=raw,file='+str((output/'disposable-sd.img').absolute())
    if expected not in command or '-no-reboot' not in command:
        raise ValueError('Reboot fixture is not the generated disposable SD machine')
    probe.inspect(expected_boot_id=boot_id)
    offset=console_path.stat().st_size
    source=('import sys,subprocess; sys.path.insert(0,"/etc/forge"); '
            'from forge_ram_identity import verify; from forge_recovery_lease_watchdog import observe; '
            'checked=verify(observe(),'+repr(probe.nonce)+','+repr(probe.kernel)+',None,mode="emulated"); '
            'assert checked["boot_id"]=='+repr(boot_id)+'; '
            'subprocess.run(["/sbin/reboot","-f"],check=True,timeout=10)')
    dispatched=subprocess.run(ssh[:-1]+['/usr/bin/python3 -I -S -c '+shlex.quote(source)],
                              capture_output=True,text=True,timeout=20)
    code=process.wait(timeout=40)
    with console_path.open('rb') as stream:
        stream.seek(offset)
        tail=stream.read(2*1024*1024).decode(errors='replace')
    if code!=0 or 'reboot: Restarting system' not in tail or 'Kernel panic' in tail:
        raise RuntimeError('Guest did not complete the requested clean emulator reboot')
    next_console=output/'console-after-reboot.log'
    with next_console.open('xb') as console:
        child=subprocess.Popen(command,stdout=console,stderr=subprocess.STDOUT)
    return child,next_console,dict(previous_boot_id=boot_id,previous_qemu_exit=code,
                                  reboot_ssh_exit=dispatched.returncode,physical_qualified=False)


def ready(process,probe,previous_boot_id):
    deadline=time.monotonic()+100
    while True:
        if process.poll() is not None: raise RuntimeError('Rebooted emulator exited before readiness')
        try:
            result=probe.inspect()
        except (RuntimeError,subprocess.TimeoutExpired):
            remaining=deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('Rebooted emulator SSH readiness deadline')
            try: process.wait(timeout=min(0.25,remaining))
            except subprocess.TimeoutExpired: pass
            continue
        if result['verification']['boot_id']==previous_boot_id:
            raise ValueError('Rebooted emulator retained the old boot UUID')
        return result


def reject_old_worker(probe,journal,pin,lease,output):
    if probe.mode!='emulated': raise ValueError('Stale worker replay qualification is emulator-only')
    plan=validate(json.loads((journal/'plan.json').read_text()),pin)
    attempt=json.loads((journal/'commit-attempt/dispatch.json').read_text())['attempt']
    if plan['binding']['boot_id']==lease.boot_id: raise ValueError('Expected a genuinely newer recovery boot')
    probe.inspect(expected_boot_id=lease.boot_id)
    pulse=LeasePulse(lease)
    def forbidden(message): raise AssertionError('Old worker reached commit preparation in a new boot')
    with (output/'stale-worker-protocol.jsonl').open('xb') as transcript, \
            (output/'stale-worker-stderr.log').open('xb') as errors:
        os.fchmod(transcript.fileno(),0o600)
        os.fchmod(errors.fileno(),0o600)
        try:
            exchange(probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(BOOTSTRAP)),
                payload(plan,pin,lease.owner,attempt),Protocol(plan,pin,attempt),prepared=forbidden,
                unmounted=forbidden,heartbeat=pulse,transcript=transcript,errors=errors,timeout=60)
        except RuntimeError:
            pass
        else:
            raise ValueError('Old worker was not rejected in the new boot')
    if (output/'stale-worker-protocol.jsonl').stat().st_size or 'Recovery boot changed' not in (
            output/'stale-worker-stderr.log').read_text():
        raise ValueError('Old worker failed for an unrelated reason or made progress')
    return dict(status='rejected-old-boot-worker',previous_boot_id=plan['binding']['boot_id'],
                current_boot_id=lease.boot_id,physical_qualified=False)
