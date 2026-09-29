"""Owner-supplied RAM worker for a bounded two-phase boot-file commit."""
from pathlib import Path


BOOTSTRAP = r'''import json,sys,types
data=sys.stdin.buffer.readline(100*1024*1024+1)
if len(data)>100*1024*1024 or not data.endswith(b'\n'): raise ValueError('Invalid bounded owner request')
request=json.loads(data)
sys.path.insert(0,'/etc/forge')
for name,source in request['modules']:
    module=types.ModuleType(name)
    sys.modules[name]=module
    exec(compile(source,'<forge-owner-commit:'+name+'>','exec'),module.__dict__)
from forge_recovery_commit_worker import run
run(request)
'''


def payload(plan, pin, owner, attempt):
    directory = Path(__file__).resolve().parent
    names = ('forge_target_backup','forge_target_files','forge_target_journal','forge_tryboot_recipe',
             'forge_trial_firmware','forge_recovery_image','forge_target_ssh','forge_recovery_claim',
             'forge_recovery_bootcommit','forge_ram_identity','forge_recovery_layout',
             'forge_recovery_lease','forge_recovery_commit_liveness','forge_recovery_commit_protocol',
             'forge_recovery_ledger','forge_recovery_commit_ledger',
             'forge_recovery_commit_worker')
    return dict(plan=plan,pin=pin,owner=owner,attempt=attempt,protocol=2,
                modules=[(name,(directory/(name+'.py')).read_text()) for name in names])


def run(request):
    import contextlib
    import io
    import json
    import os
    import select
    import signal
    import sys
    import time
    from forge_ram_identity import READER,verify
    from forge_recovery_layout import reader,root_extent
    from forge_recovery_bootcommit import execute
    from forge_recovery_commit_liveness import budget
    from forge_recovery_commit_protocol import decode
    import forge_recovery_commit_ledger as ledger
    plan,pin,owner,attempt = (request[key] for key in ('plan','pin','owner','attempt'))
    if type(request.get('protocol')) is not int or request['protocol']!=2:
        raise ValueError('Commit worker requires the fenced protocol version')
    binding = plan['binding']
    accepted = None
    last = {}
    def emit(**message):
        print(json.dumps(dict(message,attempt=attempt),allow_nan=False),flush=True)
    def observe(source):
        output=io.StringIO()
        with contextlib.redirect_stdout(output): exec(source,{})
        return json.loads(output.getvalue())
    def check():
        identity=verify(observe(READER),binding['nonce'],binding['kernel'],binding['serial'],mode=binding['mode'])
        if identity['boot_id']!=binding['boot_id']: raise ValueError('Recovery boot changed')
        return root_extent(observe(reader(binding['device'])),binding['cid'],binding['disk_id'],device=binding['device'])
    def progress(name,done,total):
        if name not in last or done-last[name]>=64*1024*1024 or done==total:
            last[name]=done
            emit(type='progress',range=name,bytes=done,total=total)
    def approve(expected_pin, expected_binding):
        nonlocal accepted
        emit(type='prepared',pin=expected_pin,binding=expected_binding)
        deadline=time.monotonic()+60
        data=bytearray()
        while not data.endswith(b'\n'):
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([sys.stdin.buffer],[],[],remaining)[0]:
                raise TimeoutError('Host commit approval deadline')
            chunk=os.read(sys.stdin.fileno(),1)
            if not chunk: raise EOFError('Host disconnected before commit approval')
            data.extend(chunk)
            if len(data)>4096: raise ValueError('Oversized commit acknowledgement')
        value=decode(data)
        if (not isinstance(value,dict) or set(value)!={'attempt','commit','boot_id','lease'} or
                value['attempt']!=attempt or value['commit']!=pin or value['boot_id']!=binding['boot_id']):
            raise ValueError('Host approval is not bound to this commit')
        accepted=value['lease']
        return dict(commit=pin,boot_id=binding['boot_id'])
    def unmounted():
        last.clear()
        emit(type='unmounted',pin=pin,boot_id=binding['boot_id'])
    def disconnected(signum,frame):
        raise RuntimeError('Recovery commit transport disconnected; completion uncertain')
    signal.signal(signal.SIGHUP,disconnected)
    signal.signal(signal.SIGTERM,disconnected)
    # Fence identity is established before a delayed worker can create intent.
    from forge_recovery_bootcommit import validate
    validate(plan,pin)
    if check()!=binding['extent']: raise ValueError('Commit layout changed before ledger')
    directory=ledger.provision(binding['boot_id'],pin)
    result=ledger.run(directory,attempt,ledger.request(plan,pin,attempt),lambda:
        execute(plan,pin,check=check,approve=approve,live_budget=lambda:budget(binding,owner,accepted),
                progress=progress,unmounted=unmounted))
    emit(type='complete',result=result)
