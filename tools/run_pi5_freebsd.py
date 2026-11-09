#!/usr/bin/env python3
"""Boot the Pi5 SD model to real FreeBSD multi-user and verify console/storage.

All inputs are regular files. Writes go to a fresh COW overlay; no physical
media or network is used. The prepared native root must contain /etc/rc,
getty/login, a secure serial tty and the matching kernel ELF in /boot/kernel.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import time
import uuid
from uconsole_emulator import qmp


def sha(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu',type=Path,required=True)
    p.add_argument('--boot-inputs',type=Path,required=True)
    p.add_argument('--disk',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--memory-gib',type=int,choices=[2,4,8],default=8)
    args=p.parse_args()
    q=args.qemu.resolve();boot=args.boot_inputs.resolve();disk=args.disk.resolve()
    inputs=[q,q.with_name('qemu-img'),boot/'FreeBSD-Image',boot/'pi5-sd.dtb',boot/'inputs.json',disk]
    for f in inputs:
        if not stat.S_ISREG(f.stat().st_mode):raise ValueError('regular file required: '+str(f))
    pins={str(f):sha(f) for f in inputs}
    manifest=json.loads((boot/'inputs.json').read_text())
    if manifest['memory_gib']!=args.memory_gib:raise ValueError('DT/machine RAM profile mismatch')
    if manifest['image_sha256']!=pins[str(boot/'FreeBSD-Image')] or manifest['derived_dtb_sha256']!=pins[str(boot/'pi5-sd.dtb')]:
        raise ValueError('prepared boot input hash mismatch')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    env={k:v for k,v in os.environ.items() if k not in ['LD_LIBRARY_PATH','DYLD_LIBRARY_PATH']}
    subprocess.run([str(q.with_name('qemu-img')),'create','-f','qcow2','-F','raw','-b',str(disk),str(out/'sd.qcow2')],check=True,env=env,capture_output=True)
    cmd=[str(q),'-M','raspi5b','-m',str(args.memory_gib)+'G','-kernel',str(boot/'FreeBSD-Image'),'-dtb',str(boot/'pi5-sd.dtb'),'-drive',f'if=sd,format=qcow2,file={out}/sd.qcow2','-display','none','-serial','stdio','-monitor','none','-nic','none','-no-reboot']
    nonce=uuid.uuid4().hex
    monitor=Path('/tmp')/('pi5-'+nonce[:16]+'.qmp')
    cmd += ['-qmp',f'unix:{monitor},server=on,wait=off']
    commands=[
        '/usr/bin/id',
        '/usr/bin/uname -a',
        '/sbin/sysctl hw.ncpu hw.physmem kern.init_path',
        '/bin/ps -ax -o pid,ppid,state,comm',
        '/sbin/mount -p',
        '/sbin/sha256 -q /boot/kernel/kernel',
        'dd if=/dev/random of=/tmp/pi5-'+nonce+' bs=1m count=1 2>/dev/null && sha256 /tmp/pi5-'+nonce+' && sync',
    ]
    token=lambda i:('__PI5_'+nonce+'_'+str(i)+'__').encode()
    logs=[];failure=None;phase='boot';index=0;data=b''
    with (out/'serial.log').open('wb') as log,(out/'qemu.log').open('wb') as err:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=err,env=env)
        sel=selectors.DefaultSelector();sel.register(proc.stdout,selectors.EVENT_READ)
        started=time.monotonic();deadline=started+180;observed=False
        def send(text):
            wire=(text+'\n').encode()
            if len(wire)>280:raise ValueError('UART frame too long')
            proc.stdin.write(wire);proc.stdin.flush()
        def next_command():
            send(commands[index]+"; printf '\\n"+token(index).decode()+"\\n'")
        try:
            while True:
                for key,_ in sel.select(.25):
                    chunk=os.read(key.fd,65536)
                    if chunk:log.write(chunk);log.flush();data+=chunk
                    else:sel.unregister(key.fileobj)
                if b'panic:' in data:raise RuntimeError('kernel panic')
                if phase=='boot' and not observed and time.monotonic()-started>20:
                    obs={'status':qmp(monitor,'query-status'),
                         'cpus':qmp(monitor,'human-monitor-command',{'command-line':'info cpus'}),
                         'registers':qmp(monitor,'human-monitor-command',{'command-line':'info registers'})}
                    (out/'boot-observation.json').write_text(json.dumps(obs,indent=2)+'\n');observed=True
                if phase=='boot' and b'login:' in data:
                    send('root');phase='login';data=b'';print('getty/login reached',flush=True)
                elif phase=='login' and b'# ' in data:
                    phase='commands';data=b'';next_command();print('root login accepted',flush=True)
                elif phase=='commands' and b'\r\n'+token(index)+b'\r\n' in data:
                    logs.append(data.decode(errors='replace'));index+=1;data=b''
                    if index<len(commands):next_command()
                    else:send('/sbin/reboot');phase='reboot';print('commands complete; reboot requested',flush=True)
                if proc.poll() is not None and not sel.get_map():break
                if time.monotonic()>deadline:raise TimeoutError('deadline in '+phase)
        except Exception as e:
            failure=repr(e)
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=5)
            remaining=proc.stdout.read();log.write(remaining or b'')
            sel.close()
    monitor.unlink(missing_ok=True)
    text=(out/'serial.log').read_text(errors='replace')
    checks={'natural_exit':proc.returncode==0 and phase=='reboot' and failure is None,
            'all_commands':index==len(commands),'four_cpus':'FreeBSD/SMP: Multiprocessor System Detected: 4 CPUs' in text,
            'a76':'CPU  0: ARM Cortex-A76' in text,'cpu_affinity':all(('Starting CPU '+str(i)+' ('+format(i<<8,'x')+')') in text for i in range(1,4)),
            'psci':'psci0:' in text,'bcm2712_sd':'sdhci_bcm27120:' in text,'rng':'bcmrng0:' in text,
            'buffers_synced':'All buffers synced.' in text,'no_panic':'panic:' not in text}
    if len(logs)==len(commands):
        checks.update(root_login='uid=0(root)' in logs[0],ncpu='hw.ncpu: 4' in logs[2],
            init=bool(re.search(r'\b1\s+0\s+\S+\s+init\b',logs[3])),
            login_process=bool(re.search(r'\b1\s+\S+\s+login\b',logs[3])),
            userspace_services='syslogd' in logs[3] and 'devd' in logs[3],
            ufs_root=bool(re.search(r'/dev/gpt/PI5ROOT\s+/\s+ufs\s+rw',logs[4])),
            matching_kernel=bool(re.search(r'(?m)^'+manifest['source_kernel_sha256']+r'\r?$',logs[5])),
            storage_roundtrip=bool(re.search(r'SHA256 .*'+nonce+r'.*= [0-9a-f]{64}',logs[6])))
        memory=re.search(r'hw.physmem: (\d+)',logs[2]);checks['full_modeled_memory']=bool(memory and args.memory_gib*1024**3*.90<int(memory[1])<=args.memory_gib*1024**3)
    checks['inputs_unchanged']=all(sha(Path(f))==h for f,h in pins.items())
    result={'ok':all(checks.values()),'checks':checks,'failure':failure,'phase':phase,'exit_code':proc.returncode,'command':cmd,'memory_gib':args.memory_gib,'inputs':pins,'console_commands':commands,'command_outputs':logs,'limitations':['SD-only digital BCM2712 D0 model','direct FreeBSD Linux-boot-ABI handoff; no VideoCore/EEPROM emulation','RP1 peripherals not implemented','no physical/electrical qualification']}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'ok':result['ok'],'checks':checks,'failure':failure}),flush=True)
    if not result['ok']:raise SystemExit(1)

if __name__=='__main__':main()
