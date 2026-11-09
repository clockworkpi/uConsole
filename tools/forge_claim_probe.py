"""Emulator-only qualification of a root claim across disposable FAT writes."""
from pathlib import Path

SOURCE = r'''
import contextlib,errno,io,json,os,types
for name,source in request['modules']:
    module=types.ModuleType(name)
    import sys
    sys.modules[name]=module
    exec(compile(source,'<forge-claim-probe:'+name+'>','exec'),module.__dict__)
from forge_ram_identity import READER,verify
from forge_recovery_layout import reader,root_extent
from forge_recovery_claim import claim_root
p=request['binding']
def observe(source):
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(source,{})
    return json.loads(output.getvalue())
def check():
    identity=verify(observe(READER),p['nonce'],p['kernel'],None,mode='emulated')
    if identity['boot_id']!=p['boot_id']: raise ValueError('Recovery boot changed')
    return root_extent(observe(reader('/dev/mmcblk1')),p['cid'],p['disk_id'],device='/dev/mmcblk1')
with claim_root('/dev/mmcblk1',p['extent'],check) as claim:
    claim.verify_guards(request['guards'])
    try:
        competing=os.open('/dev/mmcblk1p2',os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK)
    except OSError as exc:
        if exc.errno!=errno.EBUSY: raise
    else:
        os.close(competing)
        raise ValueError('Competing root claim was accepted')
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(request['boot_script'],{})
    result=json.loads(output.getvalue())
    claim.verify_guards({name:guard for name,guard in request['guards'].items() if name!='prefix'})
result['root_claim_qualified']=True
result['competing_root_claim_rejected']=True
print(json.dumps(result))
'''


def script(boot_script, binding, guards):
    directory = Path(__file__).resolve().parent
    request = dict(boot_script=boot_script, binding=binding, guards=guards,
                   modules=[(name, (directory/(name+'.py')).read_text()) for name in
                            ('forge_ram_identity', 'forge_recovery_layout', 'forge_recovery_claim')])
    return 'request='+repr(request)+'\n'+SOURCE
