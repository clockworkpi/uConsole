"""Read-only offline card digests for root and boot-transition preconditions.

This is not a backup: no source bytes are retained. It cannot authorize root
writes or normal boot release, even when a checksum matches an earlier record.
"""
import os
import hashlib
from pathlib import Path
import re
import shlex

from forge_recovery_backup import receive
from forge_recovery_lease_client import LeasePulse
from forge_target_journal import private_directory, write_record


WORKER = r'''
import contextlib,fcntl,hashlib,io,json,os,stat,struct,sys,types
for name,source in request['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source,'<forge-hash:'+name+'>','exec'),module.__dict__)
from forge_ram_identity import READER,verify
from forge_recovery_layout import reader,root_extent
p=request['plan']
def observe(source):
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(source,{})
    return json.loads(output.getvalue())
def check():
    identity=verify(observe(READER),p['nonce'],p['kernel'],p['serial'],mode=p['mode'])
    if identity['boot_id']!=p['boot_id']: raise ValueError('Recovery boot changed')
    extent=root_extent(observe(reader(p['device'])),p['cid'],p['disk_id'],device=p['device'])
    if extent!=p['extent']: raise ValueError('Recovery layout changed')
check()
fd=os.open(p['device'],os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
    info=os.fstat(fd)
    dev=open('/sys/class/block/'+p['device'].rsplit('/',1)[1]+'/dev').read().strip()
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'!=dev:
        raise ValueError('Whole-card device identity differs')
    size=bytearray(8)
    fcntl.ioctl(fd,0x80081272,size,True)
    length=p['extent']['disk_bytes']
    if struct.unpack('=Q',size)[0]!=length: raise ValueError('Whole-card length differs')
    check()
    start=p['extent']['offset_bytes']
    end=start+p['extent']['length_bytes']
    ranges={'prefix':(0,start),'root':(start,end),'suffix':(end,length)}
    hashes={name:hashlib.sha256() for name in ranges}
    whole=hashlib.sha256()
    total=0
    due=64*1024*1024
    while total<length:
        data=os.read(fd,min(1048576,length-total))
        if not data: raise ValueError('Card read ended early')
        whole.update(data)
        for name,(low,high) in ranges.items():
            begin,finish=max(total,low),min(total+len(data),high)
            if begin<finish: hashes[name].update(data[begin-total:finish-total])
        total+=len(data)
        if total>=due or total==length:
            print(json.dumps(dict(bytes_read=total,total_bytes=length)),flush=True)
            due=total+64*1024*1024
    check()
    result={name:dict(offset=low,bytes=high-low,sha256=hashes[name].hexdigest())
            for name,(low,high) in ranges.items()}
    result.update(boot_id=p['boot_id'],card=dict(bytes=length,sha256=whole.hexdigest()))
    print('FORGE_STORAGE_HASH_RESULT '+json.dumps(result),file=sys.stderr,flush=True)
finally:
    os.close(fd)
'''


def check_receipt(receipt, extent, boot_id):
    if not isinstance(receipt, dict) or set(receipt) != {'boot_id', 'card', 'prefix', 'root', 'suffix'}:
        raise ValueError('Unexpected storage hash receipt')
    if receipt['boot_id'] != boot_id:
        raise ValueError('Storage hash receipt belongs to another recovery boot')
    length, start = extent['disk_bytes'], extent['offset_bytes']
    end = start+extent['length_bytes']
    for name, bounds in {'card':(None,length), 'prefix':(0,start),
                         'root':(start,end-start), 'suffix':(end,length-end)}.items():
        value = receipt[name]
        fields = {'bytes','sha256'} if name=='card' else {'offset','bytes','sha256'}
        if (not isinstance(value, dict) or set(value) != fields or
                type(value['bytes']) is not int or value['bytes'] != bounds[1] or
                not isinstance(value['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['sha256']) or
                (name != 'card' and (type(value['offset']) is not int or value['offset'] != bounds[0]))):
            raise ValueError('Storage hash receipt range differs: '+name)
        if value['bytes'] == 0 and value['sha256'] != hashlib.sha256(b'').hexdigest():
            raise ValueError('Empty storage range has an invalid digest')
    return receipt


def capture(probe, directory, cid, disk_id, *, boot_id, device='/dev/mmcblk0', lease=None):
    if lease is not None and (lease.probe is not probe or lease.boot_id != boot_id):
        raise ValueError('Checksum lease must bind this probe and recovery boot')
    pulse = LeasePulse(lease) if lease is not None else None
    if pulse is not None:
        pulse()
    observed = probe.inspect_storage(cid, disk_id, expected_boot_id=boot_id, device=device)
    plan = dict(nonce=probe.nonce, kernel=probe.kernel, serial=probe.serial, mode=probe.mode,
                boot_id=boot_id, cid=cid, disk_id=disk_id, device=device, extent=observed['extent'])
    if lease is not None:
        plan['lease_owner'] = lease.owner
    directory = Path(directory).absolute()
    directory.mkdir(mode=0o700)
    parent = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    fd = private_directory(directory)
    result = dict(status='incomplete', mode=probe.mode, is_backup=False, root_write_authorized=False,
                  normal_boot_release_authorized=False, target_written=False)
    try:
        write_record(fd, 'plan.json', plan)
        source = Path(__file__).resolve().parent
        request = dict(plan=plan, modules=[(name, (source/(name+'.py')).read_text())
                       for name in ('forge_ram_identity', 'forge_recovery_layout')])
        script = 'request='+repr(request)+'\n'+WORKER
        maximum = min(4*1024*1024, (plan['extent']['disk_bytes']//(64*1024*1024)+2)*128)
        receipt = receive(probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(script)),
                          directory/'progress.jsonl', maximum, timeout=82800 if pulse else 240,
                          heartbeat=pulse, receipt_prefix='FORGE_STORAGE_HASH_RESULT ')
        checked = check_receipt(receipt, observed['extent'], boot_id)
        after = probe.inspect_storage(cid, disk_id, expected_boot_id=boot_id, device=device)
        if after['extent'] != observed['extent']:
            raise ValueError('Storage changed after hashing')
        result.update(status='verified-offline-storage-digests', digests=checked)
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd, 'acceptance.json', result)
        os.close(fd)
    return result
