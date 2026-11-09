"""Emulator-only FAT probe with an explicit disposable-file roundtrip mode."""
from pathlib import Path
import re


SOURCE = r'''
import contextlib,errno,io,json,os,stat,subprocess,sys
from pathlib import Path
sys.path.insert(0,'/etc/forge')
from forge_ram_identity import READER,verify
def identity():
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(READER,{})
    observed=json.loads(output.getvalue())
    result=verify(observed,request['nonce'],request['kernel'],None,mode='emulated')
    if result['boot_id']!=request['boot_id']: raise ValueError('Recovery boot changed')
    if Path('/sys/class/block/mmcblk1/device/cid').read_text().strip()!=request['cid']:
        raise ValueError('Synthetic card identity changed')
    return result
identity()
point=Path('/run/forge-bootfs-'+request['nonce'])
point.mkdir(mode=0o700)
access='rw' if request['write_roundtrip'] else 'ro'
options=access+',nodev,nosuid,noexec,uid=0,gid=0,fmask=0077,dmask=0077,iocharset=ascii,codepage=437'
mounted=False
try:
    subprocess.run(['mount','-t','vfat','-o',options,'/dev/mmcblk1p1',str(point)],check=True,timeout=10)
    mounted=True
    rows=[]
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left,_,right=line.partition(' - ')
        fields,fs=left.split(),right.split()
        if fields[4]==str(point): rows.append((fields,fs))
    if len(rows)!=1: raise ValueError('Expected exactly one fixture mount')
    fields,fs=rows[0]
    required={access,'nosuid','nodev','noexec'}
    expected=Path('/sys/class/block/mmcblk1p1/dev').read_text().strip()
    if fields[2]!=expected or fs[0]!='vfat' or not required.issubset(fields[5].split(',')):
        raise ValueError('Fixture mount source/type/options differ')
    info=point.stat()
    if stat.S_IMODE(info.st_mode)!=0o700 or info.st_uid!=0 or info.st_gid!=0:
        raise ValueError('Fixture mount is not private')
    if list(point.iterdir()): raise ValueError('Expected empty disposable boot filesystem')
    if request['write_roundtrip']:
        import types
        for name,source in request['modules']:
            module=types.ModuleType(name)
            sys.modules[name]=module
            exec(compile(source,'<forge-owner:'+name+'>','exec'),module.__dict__)
        from validate_target_fat import run
        roundtrip=run(point,mode=0o700)
        if list(point.iterdir()): raise ValueError('Fixture files or staging leftovers remain')
    else:
        try:
            fd=os.open(point/'must-not-create',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except OSError as exc:
            if exc.errno!=errno.EROFS: raise
        else:
            os.close(fd)
            raise ValueError('Read-only filesystem accepted a write')
finally:
    if mounted or os.path.ismount(point):
        subprocess.run(['umount',str(point)],check=True,timeout=10)
    point.rmdir()
identity()
result=dict(status='passed',filesystem='vfat',read_only=not request['write_roundtrip'],
            private_mount=True,unmounted=True,physical_qualified=False,root_written=False)
if request['write_roundtrip']:
    result['roundtrip_checks']=roundtrip['checks']
    result['files_restored_to_absence']=True
else:
    result['write_rejected']=True
print(json.dumps(result))
'''


def script(nonce, boot_id, kernel, cid, *, write_roundtrip=False):
    if type(write_roundtrip) is not bool:
        raise ValueError('Disposable write roundtrip requires an explicit boolean')
    if not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Expected bound fixture nonce')
    if not isinstance(boot_id, str) or not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id):
        raise ValueError('Expected bound fixture boot ID')
    if not isinstance(kernel, str) or not kernel or not isinstance(cid, str) or not re.fullmatch('[0-9a-f]{32}', cid):
        raise ValueError('Expected kernel and synthetic card identity')
    request = dict(nonce=nonce,boot_id=boot_id,kernel=kernel,cid=cid,write_roundtrip=write_roundtrip)
    if write_roundtrip:
        directory = Path(__file__).resolve().parent
        request['modules'] = [(name, (directory/(name+'.py')).read_text()) for name in
                              ('forge_target_backup', 'forge_target_files', 'validate_target_fat')]
    return 'request='+repr(request)+'\n'+SOURCE
