"""Qualify real SSH fencing without deploying an image or changing boot policy.

Retains a private host journal and a root-owned target fence. The delayed-worker
probe calls the production ledger with a harmless effect, not the image worker:
the ordinary public boot policy would otherwise reject it before ledger entry.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import uuid

from forge_recovery_journal import prepare, dispatch, reconcile
from forge_recovery_ssh import transport


def remote(host, source, payload=None):
    return subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host,
         'sudo -n python3 -c ' + shlex.quote(source)],
        input=json.dumps(payload), text=True, capture_output=True, timeout=60, check=True)


def qualify(host, output):
    output.mkdir(mode=0o700)
    capture = '''import hashlib,json,pathlib
p=pathlib.Path
print(json.dumps(dict(machine_id=p('/etc/machine-id').read_text().strip(),
 fstab_sha256=hashlib.sha256(p('/etc/fstab').read_bytes()).hexdigest(),
 boot_id=p('/proc/sys/kernel/random/boot_id').read_text().strip())))
'''
    before = json.loads(remote(host, capture).stdout)
    token = uuid.uuid4().hex
    plan = dict(schema=2, kind='private-recovery-image', host=host,
                machine_id=before['machine_id'], fstab_sha256=before['fstab_sha256'],
                boot_source='/dev/mmcblk0p1', source='/var/tmp/forge-unstarted-' + token,
                destination='/boot/firmware/forge-recovery-' + token + '.img',
                sha256='0' * 64, size=1, stage_token=token, preimage={'kind': 'absent'})
    journal = output / 'journal'
    digest = prepare(journal, plan)['plan_sha256']
    def interrupted(*args):
        raise TimeoutError('Qualification: dispatch interrupted before worker started')
    try:
        dispatch(journal, 'apply', digest, interrupted)
    except TimeoutError:
        pass
    original = [p.read_bytes() for p in sorted(journal.glob('event-*.json'))]
    result = reconcile(journal, digest, transport)
    assert result['outcome']['status'] == 'fenced-not-started'
    assert original == [p.read_bytes() for p in sorted(journal.glob('event-*.json'))]
    root = Path(__file__).resolve().parent
    names = ('forge_target_backup', 'forge_target_journal', 'forge_recovery_ledger')
    probe = '''import json,sys,types
r=json.load(sys.stdin)
for name,source in r['modules']:
 m=types.ModuleType(name);sys.modules[name]=m
 exec(compile(source,'<forge-fence-qualification>','exec'),m.__dict__)
from forge_recovery_ledger import provision,run
called=[]
try:
 run(provision(r['request']['plan_sha256']),r['request']['nonce'],r['request'],lambda: called.append(True))
except RuntimeError as e:
 if str(e) != 'Target request was fenced before starting': raise
 if called: raise RuntimeError('Fenced effect ran')
 print(json.dumps(dict(delayed_effect_ran=False, rejection=str(e))))
else:
 raise RuntimeError('Delayed fenced worker was accepted')
'''
    delayed = json.loads(remote(host, probe, dict(request=result['outcome']['request'],
                        modules=[(n, (root / (n + '.py')).read_text()) for n in names])).stdout)
    repeated = transport(plan, 'fence-apply', result['nonce'], digest)
    assert repeated == result
    after = json.loads(remote(host, capture).stdout)
    assert before == after
    evidence = dict(before=before, after=after, fence=result, delayed_worker=delayed,
                    repeated_fence_identical=True, original_history_preserved=True,
                    scope='Pre-start interruption simulation; real SSH fence and delayed ledger worker. '
                          'No image publication, boot change, or mid-effect disconnect tested.')
    from forge_target_journal import private_directory, write_record
    import os
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
