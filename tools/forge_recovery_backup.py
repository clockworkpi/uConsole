"""Bounded offline root or whole-card byte backup; no restore/write API."""
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import subprocess
import time

from forge_target_journal import private_directory, write_record
from forge_recovery_lease_client import LeasePulse

HOST_RESERVE_BYTES = 128*1024*1024


WORKER = r'''
import contextlib,fcntl,gzip,hashlib,io,json,os,stat,struct,sys,types
for name,source in request['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source,'<forge-backup:'+name+'>','exec'),module.__dict__)
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
    if extent!=p['extent']: raise ValueError('Recovery storage changed')
check()
sources={'root-partition-bytes':dict(device=p['extent']['device'],length_bytes=p['extent']['length_bytes']),
         'whole-card-bytes':dict(device=p['device'],length_bytes=p['extent']['disk_bytes'])}
if p['backup_kind'] not in sources or p['source']!=sources[p['backup_kind']]:
    raise ValueError('Backup source differs from the verified storage scope')
partition=p['source']['device']
fd=os.open(partition,os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK)
try:
    info=os.fstat(fd)
    dev=open('/sys/class/block/'+partition.rsplit('/',1)[1]+'/dev').read().strip()
    if not stat.S_ISBLK(info.st_mode) or f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'!=dev:
        raise ValueError('Root partition device differs')
    size=bytearray(8)
    fcntl.ioctl(fd,0x80081272,size,True)
    length=p['source']['length_bytes']
    if struct.unpack('=Q',size)[0]!=length: raise ValueError('Root partition size differs')
    check()
    remaining=length
    digest=hashlib.sha256()
    with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',compresslevel=1,mtime=0) as output:
        while remaining:
            data=os.read(fd,min(1048576,remaining))
            if not data: raise ValueError('Root partition read ended early')
            digest.update(data)
            output.write(data)
            remaining-=len(data)
    check()
    print('FORGE_BACKUP_RESULT '+json.dumps(dict(bytes=length,sha256=digest.hexdigest(),
                                              boot_id=p['boot_id'])),file=sys.stderr,flush=True)
finally:
    os.close(fd)
'''


def receive(argv, destination, maximum, *, timeout=240, heartbeat=None,
            receipt_prefix='FORGE_BACKUP_RESULT '):
    """Bound output, time and available space; retain partial files on failure."""
    bound = 82800 if heartbeat is not None else 240
    if (type(maximum) is not int or maximum <= 0 or type(timeout) not in (int, float) or
            not math.isfinite(timeout) or not 0 < timeout <= bound or
            (heartbeat is not None and not callable(heartbeat)) or
            not isinstance(receipt_prefix, str) or not re.fullmatch('[A-Z_]{1,64} ', receipt_prefix)):
        raise ValueError('Invalid bounded backup transfer')
    process = None
    stderr = bytearray()
    count = 0
    deadline = time.monotonic()+timeout
    try:
        with destination.open('xb') as output:
            os.fchmod(output.fileno(),0o600)
            try:
                if heartbeat is not None:
                    heartbeat()
                process = subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ,'data')
                    selector.register(process.stderr,selectors.EVENT_READ,'error')
                    while selector.get_map():
                        if heartbeat is not None:
                            heartbeat()
                        remaining = deadline-time.monotonic()
                        if remaining <= 0: raise TimeoutError('Backup transfer deadline')
                        events = selector.select(min(remaining,30))
                        if not events: raise TimeoutError('Backup transfer stopped making progress')
                        for key,_ in events:
                            data = os.read(key.fd,65536)
                            if not data:
                                selector.unregister(key.fileobj)
                                continue
                            if key.data == 'error':
                                stderr.extend(data)
                                if len(stderr)>65536: raise ValueError('Oversized backup diagnostics')
                            else:
                                count += len(data)
                                if count>maximum: raise ValueError('Backup exceeds compressed size bound')
                                if shutil.disk_usage(destination.parent).free < HOST_RESERVE_BYTES+len(data):
                                    raise OSError('Backup would exhaust reserved host free space')
                                output.write(data)
                if process.wait(timeout=max(0.01,deadline-time.monotonic())):
                    raise RuntimeError('Recovery backup transport failed; partial artifact retained')
            finally:
                output.flush()
                os.fsync(output.fileno())
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
    receipts = [line.removeprefix(receipt_prefix) for line in stderr.decode().splitlines()
                if line.startswith(receipt_prefix)]
    if len(receipts)!=1: raise ValueError('Missing or ambiguous backup completion receipt')
    return json.loads(receipts[0])


def verify_archive(path, length, digest, *, heartbeat=None):
    hashed = hashlib.sha256()
    total = 0
    with gzip.open(path,'rb') as source:
        while data := source.read(min(1048576,length-total+1)):
            if heartbeat is not None:
                heartbeat()
            total += len(data)
            if total>length: raise ValueError('Backup expands beyond the approved root extent')
            hashed.update(data)
    if total!=length or hashed.hexdigest()!=digest:
        raise ValueError('Backup length or source checksum differs')
    return {'bytes':total,'sha256':hashed.hexdigest()}


def backup(probe, directory, cid, disk_id, *, boot_id, device='/dev/mmcblk0', compressed_limit=None,
           lease=None, whole_card=False):
    if type(whole_card) is not bool:
        raise ValueError('Whole-card backup selection must be explicit boolean')
    if compressed_limit is not None and (type(compressed_limit) is not int or compressed_limit <= 0):
        raise ValueError('Compressed backup limit must be a positive byte count')
    if lease is not None and (lease.probe is not probe or lease.boot_id != boot_id):
        raise ValueError('Backup lease must be bound to this exact probe and boot')
    pulse = LeasePulse(lease) if lease is not None else None
    if pulse is not None:
        pulse()
    inspected = probe.inspect_storage(cid,disk_id,expected_boot_id=boot_id,device=device)
    extent = inspected['extent']
    source = dict(device=device if whole_card else extent['device'],
                  length_bytes=extent['disk_bytes'] if whole_card else extent['length_bytes'])
    length = source['length_bytes']
    maximum = length+length//100+1048576
    if compressed_limit is not None:
        maximum = min(maximum,compressed_limit)
    plan = dict(nonce=probe.nonce,kernel=probe.kernel,serial=probe.serial,mode=probe.mode,
                boot_id=boot_id,device=device,cid=cid,disk_id=disk_id,extent=inspected['extent'],
                maximum_compressed_bytes=maximum, source=source,
                backup_kind='whole-card-bytes' if whole_card else 'root-partition-bytes')
    if lease is not None:
        plan['lease_owner'] = lease.owner
        plan['transfer_timeout_seconds'] = 82800
    directory = Path(directory).absolute()
    available = shutil.disk_usage(directory.parent).free
    if available < maximum + HOST_RESERVE_BYTES:
        raise OSError('Insufficient host space for bounded backup plus reserve')
    plan['host_space_preflight'] = dict(available_bytes=available,
                                       required_bytes=maximum+HOST_RESERVE_BYTES,
                                       reserve_bytes=HOST_RESERVE_BYTES)
    directory.mkdir(mode=0o700)
    parent = os.open(directory.parent,os.O_RDONLY|os.O_DIRECTORY)
    try: os.fsync(parent)
    finally: os.close(parent)
    fd = private_directory(directory)
    record = {'status':'incomplete','restore_authorized':False,
              'scope':'offline whole-card bytes' if whole_card else 'offline root-partition bytes',
              'whole_system_backup':False,'filesystem_consistency_qualified':False,'mode':probe.mode}
    try:
        write_record(fd,'plan.json',plan)
        root = Path(__file__).resolve().parent
        request = dict(plan=plan,modules=[(name,(root/(name+'.py')).read_text()) for name in
                                        ('forge_ram_identity','forge_recovery_layout')])
        script = 'request='+repr(request)+'\n'+WORKER
        path = directory/('card.img.gz' if whole_card else 'root.img.gz')
        receipt = receive(probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(script)),path,
                          maximum, timeout=82800 if pulse is not None else 240, heartbeat=pulse)
        if set(receipt)!={'bytes','sha256','boot_id'} or receipt['bytes']!=length or receipt['boot_id']!=boot_id:
            raise ValueError('Backup receipt does not match the approved session/extent')
        record['card' if whole_card else 'root'] = verify_archive(path,length,receipt['sha256'],heartbeat=pulse)
        if pulse is not None:
            pulse()
        probe.inspect_storage(cid,disk_id,expected_boot_id=boot_id,device=device)
        record.update(status='verified-card-byte-backup' if whole_card else 'verified-root-backup',
                      compressed_bytes=path.stat().st_size)
    except BaseException as exc:
        record['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd,'acceptance.json',record)
        os.close(fd)
    return record
