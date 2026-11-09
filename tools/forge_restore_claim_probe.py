"""Emulator-only writable-claim roundtrip on the validator's small synthetic SD."""
from pathlib import Path

SOURCE = r'''
import contextlib,errno,hashlib,io,json,os,sys,types
for name,source in request['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source,'<forge-restore-claim-probe:'+name+'>','exec'),module.__dict__)
from forge_ram_identity import READER,verify
from forge_recovery_layout import reader,root_extent
from forge_recovery_restore_claim import claim_restore_root
p=request['binding']
if p['mode']!='emulated' or p['device']!='/dev/mmcblk1' or p['extent']['disk_bytes']>128*1024*1024:
    raise ValueError('Root write probe requires a small disposable emulator card')
def observe(source):
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(source,{})
    return json.loads(output.getvalue())
def check():
    identity=verify(observe(READER),p['nonce'],p['kernel'],None,mode='emulated')
    if identity['boot_id']!=p['boot_id']: raise ValueError('Recovery boot changed')
    return root_extent(observe(reader(p['device'])),p['cid'],p['disk_id'],device=p['device'])
if check()!=p['extent']: raise ValueError('Disposable fixture extent changed')
pin=hashlib.sha256(json.dumps(dict(binding=p,guards=request['guards']),sort_keys=True).encode()).hexdigest()
def approve(expected,boot):
    if expected!=pin or boot!=p['boot_id']: raise ValueError('Fixture approval differs')
    return dict(restore=pin,boot_id=boot)
with claim_restore_root(p['device'],p['extent'],request['guards'],pin=pin,boot_id=p['boot_id'],
                        check=check,authorize=approve) as claim:
    for access in (os.O_RDONLY,os.O_RDWR):
        try:
            competing=os.open(p['device']+'p2',access|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno!=errno.EBUSY: raise
        else:
            os.close(competing)
            raise ValueError('Competing exclusive root claim was accepted')
    original=os.pread(claim.root_fd,512,0)
    if len(original)!=512: raise ValueError('Synthetic root read ended early')
    changed=bytes(value^0xff for value in original)
    if os.pwrite(claim.root_fd,changed,0)!=512: raise OSError('Short fixture root write')
    os.fsync(claim.root_fd)
    if os.pread(claim.root_fd,512,0)!=changed: raise ValueError('Fixture root write did not persist')
    # This is the explicitly planned second half of the synthetic roundtrip,
    # never an exception-handler rollback of an uncertain physical write.
    if os.pwrite(claim.root_fd,original,0)!=512: raise OSError('Short fixture root restoration')
    os.fsync(claim.root_fd)
    claim.verify_guards({'root':request['guards']['root']})
print(json.dumps(dict(status='passed',root_written=True,root_restored=True,
    competing_read_claim_rejected=True,competing_write_claim_rejected=True,
    protected_ranges_unchanged=True,physical_qualified=False)))
'''


def script(binding,guards):
    if (not isinstance(binding,dict) or binding.get('mode')!='emulated' or
            binding.get('device')!='/dev/mmcblk1' or
            type(binding.get('extent',{}).get('disk_bytes')) is not int or
            not 0<binding['extent']['disk_bytes']<=128*1024*1024):
        raise ValueError('Root write probe requires a small disposable emulator card')
    directory=Path(__file__).resolve().parent
    request=dict(binding=binding,guards=guards,modules=[(name,(directory/(name+'.py')).read_text()) for name in
        ('forge_ram_identity','forge_recovery_layout','forge_recovery_claim','forge_recovery_restore_claim')])
    return 'request='+repr(request)+'\n'+SOURCE
