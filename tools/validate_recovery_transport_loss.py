"""Compose host journal and target ledger across a deliberately dropped SSH link.

Qualification-only transport maps the image destination to a disposable private
directory. It does not exercise boot-policy enforcement or grant deployment
authority. A real completed publication is left without a host acknowledgement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import subprocess
import uuid

from forge_recovery_journal import prepare, dispatch, reconcile
from forge_target_journal import private_directory, write_record
from validate_recovery_fence import remote


WORKER = '''import json,sys,types,os,select
from pathlib import Path
r=json.loads(sys.stdin.readline())
for name,source in r['modules']:
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(source,'<forge-loss-qualification>','exec'),m.__dict__)
from forge_recovery_ledger import run,fence
from forge_recovery_image import publish,remove
from forge_recovery_journal import acknowledgement,validate
p=validate(r['plan']); d=r['direction']; n=r['nonce']; h=r['digest']
if Path('/etc/machine-id').read_text().strip()!=p['machine_id']: raise RuntimeError('Wrong target')
root=Path(r['fixture'])
request=dict(plan_sha256=h,direction=d.removeprefix('fence-'),nonce=n)
if d.startswith('fence-'):
 result=dict(kind='fence',nonce=n,plan_sha256=h,machine_id=p['machine_id'],
             outcome=fence(root/'ledger',n,request))
else:
 def effect():
  args=(str(root/'destination.img'),p['sha256'],p['size'],p['stage_token'])
  if d=='apply': publish(p['source'],*args)
  elif d=='restore': remove(*args)
  else: raise RuntimeError('Bad direction')
  return acknowledgement(p,d,n,h)
 result=run(root/'ledger',n,request,effect)
if r['drop']:
 print('DURABLE-RECEIPT',flush=True)
 # Bounded pause before acknowledgement; EOF or timeout ends the worker.
 select.select([sys.stdin],[],[],15)
 sys.exit(0)
print(json.dumps(result))
'''


def qualify(host, output):
    output.mkdir(mode=0o700)
    setup = '''import os,json,tempfile,hashlib
from pathlib import Path
root=Path(tempfile.mkdtemp(prefix='forge-ssh-loss-',dir='/var/tmp'))
(root/'ledger').mkdir(mode=0o700)
payload=b'forge-ssh-loss-fixture\\n'*100000
with os.fdopen(os.open(root/'source.img',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:
 f.write(payload);f.flush();os.fsync(f.fileno())
print(json.dumps(dict(fixture=str(root),sha256=hashlib.sha256(payload).hexdigest(),size=len(payload),
 machine_id=Path('/etc/machine-id').read_text().strip(),
 fstab_sha256=hashlib.sha256(Path('/etc/fstab').read_bytes()).hexdigest())))
'''
    fixture = json.loads(remote(host, setup).stdout)
    token = uuid.uuid4().hex
    plan = dict(schema=2, kind='private-recovery-image', host=host, machine_id=fixture['machine_id'],
                fstab_sha256=fixture['fstab_sha256'], boot_source='/dev/mmcblk0p1',
                source=fixture['fixture'] + '/source.img',
                destination='/boot/firmware/forge-recovery-' + token + '.img',
                sha256=fixture['sha256'], size=fixture['size'], stage_token=token, preimage={'kind': 'absent'})
    journal = output / 'journal'
    digest = prepare(journal, plan)['plan_sha256']
    root = Path(__file__).resolve().parent
    names = ('forge_target_backup', 'forge_target_files', 'forge_target_journal',
             'forge_recovery_image', 'forge_recovery_journal', 'forge_recovery_ledger')
    modules = [(name, (root / (name + '.py')).read_text()) for name in names]
    def transport(p, direction, nonce, plan_digest, drop=False):
        payload = dict(plan=p, direction=direction, nonce=nonce, digest=plan_digest,
                       fixture=fixture['fixture'], modules=modules, drop=drop)
        if not drop:
            # Worker consumes a single JSON line, then no additional input.
            return json.loads(remote(host, WORKER, payload).stdout)
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
                   'sudo -n python3 -c ' + shlex.quote(WORKER)]
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                process.stdin.write(json.dumps(payload) + '\n')
                process.stdin.flush()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    if not selector.select(30) or process.stdout.readline().strip() != 'DURABLE-RECEIPT':
                        raise RuntimeError('Target did not reach durable receipt boundary')
                process.terminate()  # Drop this SSH connection before receiving an acknowledgement.
                process.wait(timeout=10)
                raise ConnectionError('Qualification dropped SSH after durable target receipt')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
    try:
        dispatch(journal, 'apply', digest, lambda *args: transport(*args, drop=True))
    except ConnectionError:
        pass
    original = [p.read_bytes() for p in sorted(journal.glob('event-*.json'))]
    resolution = reconcile(journal, digest, transport)
    if resolution['outcome']['status'] != 'completed':
        raise RuntimeError('Lost SSH acknowledgement did not resolve from durable receipt')
    if original != [p.read_bytes() for p in sorted(journal.glob('event-*.json'))]:
        raise RuntimeError('Original uncertainty history was altered')
    restored = dispatch(journal, 'restore', digest, transport)
    evidence = dict(status='passed', fixture=fixture, resolution=resolution, restored=restored,
                    original_history_preserved=True, boot_paths_changed=False,
                    scope='Real SSH disconnect after fixture publication; boot-policy worker not exercised')
    fd = private_directory(output)
    try:
        write_record(fd, 'acceptance.json', evidence)
    finally:
        os.close(fd)
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(qualify(args.host, args.output), indent=2))
