"""Synthetic CONFIG install/release qualification, never physical dispatch."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import uuid

from forge_recovery_hold import compile_hold
from forge_trial_firmware import compile_recovery_recipe, paths, NATIVE
from forge_tryboot_recipe import CONFIG, CMDLINE
from forge_recovery_bootplan import compile_transition
from forge_recovery_bootcommit import plan_digest


def fixture(nonce):
    def record(path, data):
        return dict(path=path, kind='file', data=base64.b64encode(data).decode(), size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(), mode=0o700, uid=0, gid=0,
                    atime_ns=1577836800000000000, mtime_ns=1577836800000000000, xattrs={})
    original = dict(schema=1, machine_id='a'*32, files=[])
    for path in paths(nonce):
        data = {CONFIG:b'[all]\nkernel=kernel8.img\n', CMDLINE:b'root=PARTUUID=1234-02 rootwait ro\n',
                **{name:b'disposable native firmware' for name in NATIVE}}.get(path)
        original['files'].append(record(path, data) if data is not None else dict(path=path, kind='absent'))
    image_data = b'disposable nonbootable image for CONFIG transaction testing\n'
    image = dict(schema=2, kind='private-recovery-image', host='disposable.invalid',
                 machine_id=original['machine_id'], fstab_sha256='b'*64, boot_source='/dev/mmcblk1p1',
                 source='/private/fixture.img', destination='/boot/firmware/forge-recovery-'+nonce+'.img',
                 sha256=hashlib.sha256(image_data).hexdigest(), size=len(image_data),
                 stage_token=nonce, preimage=dict(kind='absent'))
    bundle = dict(schema=1, revision='d'*40, files=[])
    for name in ('start4.elf','fixup4.dat'):
        data = ('disposable alternate '+name).encode()
        bundle['files'].append(dict(name=name, data=base64.b64encode(data).decode(), size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            url='https://raw.githubusercontent.com/raspberrypi/firmware/'+'d'*40+'/boot/'+name))
    recipe = compile_recovery_recipe(original, nonce, bundle, image)
    template = original['files'][0]
    outputs = {item['path']:dict(template, **item) for item in recipe['files']}
    staged = copy.deepcopy(original)
    staged['files'] = [outputs.get(item['path'], item) for item in staged['files']]
    return compile_hold(original, staged, nonce, bundle, image), image_data


SOURCE = r'''
import contextlib,io,json,os,stat,sys,time,types
from pathlib import Path
sys.path.insert(0,'/etc/forge')
for name,source in request['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source,'<forge-bootcommit-probe:'+name+'>','exec'),module.__dict__)
from forge_ram_identity import READER,verify
from forge_recovery_layout import reader,root_extent
from forge_recovery_claim import claim_root
from forge_recovery_bootcommit import mounted_boot,execute
from forge_target_files import apply_file
b=request['binding']
def observe(source):
    output=io.StringIO()
    with contextlib.redirect_stdout(output): exec(source,{})
    return json.loads(output.getvalue())
def check():
    identity=verify(observe(READER),b['nonce'],b['kernel'],None,mode='emulated')
    if identity['boot_id']!=b['boot_id']: raise ValueError('Emulated recovery boot changed')
    return root_extent(observe(reader('/dev/mmcblk1')),b['cid'],b['disk_id'],device='/dev/mmcblk1')
if b['device']!='/dev/mmcblk1' or b['mode']!='emulated':
    raise ValueError('Disposable CONFIG probe requires the synthetic emulator card')
if check()!=b['extent']: raise ValueError('Disposable CONFIG probe layout differs')
if request['phase']=='seed':
    with claim_root('/dev/mmcblk1',b['extent'],check) as claim:
        claim.verify_guards({'root':request['root_guard']})
        with mounted_boot('/dev/mmcblk1p1',request['token']) as point:
            if list(point.iterdir()): raise ValueError('Expected empty disposable boot filesystem')
            for index,record in enumerate(request['review']['before']['files']):
                if record['kind']=='absent': continue
                path=str(point/Path(record['path']).name)
                apply_file(dict(path=path,kind='absent'),dict(record,path=path),stage_token=f'{index+1:032x}')
            image=request['review']['image_dependency']
            with (point/Path(image['path']).name).open('xb') as stream:
                os.fchmod(stream.fileno(),0o700)
                stream.write(request['image_data'])
                stream.flush()
                os.fsync(stream.fileno())
        claim.verify_guards({'root':request['root_guard']})
    print(json.dumps(dict(status='seeded-disposable-boot',root_written=False)))
else:
    if request['phase']!='commit': raise ValueError('Unknown disposable CONFIG phase')
    pin=request['pin']
    def budget():
        from forge_recovery_lease import Lease
        lease=Lease(**json.loads(Path('/run/forge-lease/deadline.json').read_text()))
        now=time.monotonic()
        lease.check(now)
        if (lease.nonce,lease.boot_id,lease.owner)!=(b['nonce'],b['boot_id'],request['owner']):
            raise ValueError('Disposable commit lease differs')
        ready=json.loads(Path('/run/forge-watchdog.ready').read_text())
        if ready.get('lease') is not True or type(ready.get('pid')) is not int or ready['pid']<=1:
            raise ValueError('Disposable watchdog readiness differs')
        os.kill(ready['pid'],0)
        expected=Path('/sys/class/watchdog/watchdog0/dev').read_text().strip()
        held=False
        for path in Path('/proc',str(ready['pid']),'fd').iterdir():
            try: info=path.stat()
            except FileNotFoundError: continue
            if stat.S_ISCHR(info.st_mode) and f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}'==expected:
                held=True
        if not held or Path('/sys/class/watchdog/watchdog0/state').read_text().strip()!='active':
            raise ValueError('Disposable watchdog owner/device differs')
        return lease.deadline-time.monotonic()
    def approve(digest,binding):
        # Local acknowledgement is ONLY for this emulator fixture. Production
        # must perform the separate pinned host approval/renewal handshake.
        if digest!=pin or binding!=b: raise ValueError('Disposable approval differs')
        return dict(commit=pin,boot_id=b['boot_id'])
    result=execute(request['plan'],pin,check=check,approve=approve,live_budget=budget)
    print(json.dumps(result))
'''


def script(request):
    request = dict(request)
    directory = Path(__file__).resolve().parent
    request['modules'] = [(name,(directory/(name+'.py')).read_text()) for name in (
        'forge_target_backup','forge_target_files','forge_target_journal','forge_tryboot_recipe',
        'forge_trial_firmware','forge_recovery_image','forge_target_ssh','forge_recovery_claim',
        'forge_recovery_bootcommit','forge_ram_identity','forge_recovery_layout')]
    return 'request='+repr(request)+'\n'+SOURCE


def transition(review, binding, host_hashes, expected_root, operation):
    extent = binding['extent']
    start, length, total = extent['offset_bytes'], extent['length_bytes'], extent['disk_bytes']
    root = dict(offset=start, bytes=length, sha256=host_hashes['root_sha256'])
    observation = dict(status='verified-offline-storage-digests', mode='emulated', is_backup=False,
        root_write_authorized=False, normal_boot_release_authorized=False, target_written=False,
        digests=dict(boot_id=binding['boot_id'], card=dict(bytes=total,sha256=host_hashes['sha256']),
            prefix=dict(offset=0,bytes=start,sha256=host_hashes['protected_prefix_sha256']), root=root,
            suffix=dict(offset=total,bytes=0,sha256=hashlib.sha256(b'').hexdigest())))
    if start+length != total:
        raise ValueError('Disposable CONFIG fixture must end at the root partition')
    plan = compile_transition(review,binding,observation,expected_root,operation)
    plan['stage_token'] = uuid.uuid4().hex
    return plan, plan_digest(plan)
