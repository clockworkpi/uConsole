"""RAM recovery/watchdog test with optional read-only probing of synthetic SD."""
import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
from pathlib import Path
import socket
import struct
import subprocess
import time
import uuid

from validate_recovery_ssh import client
from validate_recovery_guest_ssh import check_output
from forge_ram_transport import RecoveryProbe
from forge_recovery_backup import backup as backup_partition
from forge_recovery_lease_client import BackupLeaseClient
from forge_lease_watchdog_fault import SOURCE as LEASE_FAULT
from forge_bootfs_probe import script as bootfs_script
from validate_target_fat import CHECKS as FAT_CHECKS
from forge_recovery_hash import capture as capture_storage_hash
from forge_claim_probe import script as claim_probe_script
import forge_bootcommit_probe
from forge_recovery_commit_transport import dispatch as dispatch_boot_commit
from forge_target_journal import private_directory, write_record
from forge_recovery_commit_reconcile import reconcile as reconcile_boot_commit
import forge_commit_fault_probe
import forge_recovery_reboot_probe
from forge_restore_claim_probe import script as restore_claim_script
import forge_restore_stream_probe
import forge_deploy_stream_probe
import forge_recovery_session_probe


def server_key(client_key, public_path=None):
    if public_path is None:
        text = subprocess.check_output(['ssh-keygen', '-y', '-f', str(client_key)], text=True)
    else:
        with Path(public_path).open('rb') as stream:
            data = stream.read(4097)
        if len(data) > 4096:
            raise ValueError('Oversized recovery server public key')
        text = data.decode('ascii')
    if len(text.strip().splitlines()) != 1:
        raise ValueError('Expected one pinned recovery server key')
    fields = text.strip().split(maxsplit=2)
    if len(fields) < 2 or fields[0] != 'ssh-ed25519':
        raise ValueError('Expected a pinned Ed25519 recovery server key')
    wire = base64.b64decode(fields[1], validate=True)
    prefix = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32)
    if len(wire) != len(prefix)+32 or not wire.startswith(prefix):
        raise ValueError('Invalid recovery server key encoding')
    return fields[0] + ' ' + fields[1]


def storage_digests(path, boot_offset, root_offset):
    size = path.stat().st_size
    if not 0 < boot_offset < root_offset < size:
        raise ValueError('Invalid synthetic protected ranges')
    bounds = {'prefix': (0, boot_offset), 'boot': (boot_offset, root_offset),
              'root': (root_offset, size), 'protected_prefix': (0, root_offset)}
    hashed = {name: hashlib.sha256() for name in bounds}
    whole, total = hashlib.sha256(), 0
    with path.open('rb') as source:
        while data := source.read(1048576):
            whole.update(data)
            for name, (lower, upper) in bounds.items():
                start, end = max(total, lower), min(total+len(data), upper)
                if start < end:
                    hashed[name].update(data[start-total:end-total])
            total += len(data)
    if total != size:
        raise ValueError('Synthetic storage size changed while hashing')
    return dict(sha256=whole.hexdigest(), **{name+'_sha256': value.hexdigest() for name,value in hashed.items()})


def verify_storage(path, fixture, *, allow_boot_changes=False, expected_sha256=None, root_write_roundtrip=False):
    if type(allow_boot_changes) is not bool or type(root_write_roundtrip) is not bool:
        raise ValueError('Storage write scope requires explicit booleans')
    if path.stat().st_size != fixture['bytes']:
        raise ValueError('Synthetic storage size changed')
    observed = storage_digests(path, fixture['boot_offset'], fixture['root_offset'])
    for name in ('prefix_sha256', 'root_sha256'):
        if observed[name] != fixture[name]:
            raise ValueError('Synthetic storage changed outside the boot partition: '+name)
    if not allow_boot_changes and observed['sha256'] != fixture['sha256']:
        raise ValueError('Read-only synthetic storage changed')
    if expected_sha256 is not None and observed['sha256'] != expected_sha256:
        raise ValueError('Synthetic storage changed after the boot-file probe')
    return dict(fixture, **dict(observed, initial_sha256=fixture['sha256'],
                unchanged=observed['sha256']==fixture['sha256'],
                protected_prefix_unchanged=True, root_unchanged=True,
                boot_changes_allowed=allow_boot_changes, attached_read_only=False,
                probe_read_only=not (allow_boot_changes or root_write_roundtrip)))


def storage_fixture(path, *, boot_filesystem=False):
    """Create only a new synthetic image, never accept an existing device/image."""
    if type(boot_filesystem) is not bool:
        raise ValueError('Boot filesystem selection must be an explicit boolean')
    size, root_start = 64*1024*1024, 16384
    if boot_filesystem:
        size, root_start = 128*1024*1024, 139264
    header = bytearray(512)
    struct.pack_into('<I', header, 440, 0xF047E001)
    header[450], header[466] = 0x0c, 0x83
    struct.pack_into('<II', header, 454, 8192, root_start-8192)
    struct.pack_into('<II', header, 470, root_start, size//512-root_start)
    header[510:] = b'\x55\xaa'
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.truncate(size)
        stream.write(header)
        if boot_filesystem:
            boot = path.with_name('disposable-boot-fat.img')
            with boot.open('xb') as part:
                os.fchmod(part.fileno(), 0o600)
                part.truncate((root_start-8192)*512)
            subprocess.run(['mkfs.fat','-F','32','-n','FORGEPROBE',str(boot)],
                           capture_output=True, check=True, timeout=30)
            stream.seek(8192*512)
            with boot.open('rb') as part:
                shutil.copyfileobj(part, stream, 1048576)
        stream.seek(root_start*512)
        stream.write(b'FORGE DISPOSABLE ROOT EXTENT\n')
        stream.flush()
        os.fsync(stream.fileno())
    return dict(disk_id='f047e001', bytes=size, boot_offset=8192*512, root_offset=root_start*512,
                root_length=size-root_start*512, mbr_sha256=hashlib.sha256(header).hexdigest(),
                **storage_digests(path, 8192*512, root_start*512))


def verify_expiry(returncode, elapsed, text):
    if (returncode != 0 or not 8 <= elapsed <= 35 or
            any(s in text for s in ('Kernel panic', 'Forge recovery failed',
                                    'reboot: Restarting system', 'terminating on signal'))):
        raise RuntimeError('Guest exit did not qualify modeled watchdog expiry')


def verify_lease_expiry(returncode, elapsed, remaining, text):
    if (not 0 < remaining <= 300 or not max(0, remaining - 15) <= elapsed <= remaining + 120 or
            returncode != 0 or 'reboot: Restarting system' not in text or
            any(marker in text for marker in (
                'Kernel panic', 'Forge recovery failed', 'terminating on signal'))):
        raise ValueError('Guest did not qualify bounded software lease expiry')


def network_samples(text):
    fields = ('present', 'ipv4', 'associated_seen', 'assoc_reject_seen',
              'auth_disabled_seen', 'lease_bound')
    pattern = 'Forge recovery network: ' + ' '.join(name + '=([01])' for name in fields)
    lines = text.splitlines()
    if not 1 <= len(lines) <= 6:
        raise ValueError('Expected bounded recovery network samples')
    result = []
    for line in lines:
        match = re.fullmatch(pattern, line)
        if not match:
            raise ValueError('Unexpected recovery network diagnostic content')
        result.append(dict(zip(fields, map(int, match.groups()))))
    return result


def run(qemu, kernel, dtb, image, digest, key, output, require_python=False, storage=False, backup_root=False,
        interrupt_backup=False, require_network_observer=False, require_lease=False, lease_watchdog_loss=False,
        lease_soak_seconds=0, server_public_key=None, backup_card=False, boot_filesystem=False,
        boot_write_roundtrip=False, hash_storage=False, boot_commit_roundtrip=False, boot_commit_transport=False,
        boot_commit_interruption=False, boot_commit_reboot=False, boot_inspector_interruption=False,
        root_write_claim=False, root_restore_stream=False, root_restore_lost_completion=False,
        root_restore_interruption=False, root_deploy_stream=False, root_deploy_lost_completion=False,
        root_deploy_interruption=False, durable_lease=False):
    if type(durable_lease) is not bool or durable_lease and not require_lease:
        raise ValueError('Durable session validation requires the disposable recovery lease')
    if (type(root_deploy_interruption) is not bool or root_deploy_interruption and
            (not root_deploy_stream or root_deploy_lost_completion)):
        raise ValueError('Deployment interruption requires a distinct disposable stream/reboot trial')
    if (type(root_deploy_lost_completion) is not bool or
            root_deploy_lost_completion and not root_deploy_stream):
        raise ValueError('Deployment completion loss requires the disposable deployment stream')
    if (type(root_deploy_stream) is not bool or root_deploy_stream and
            (not boot_commit_transport or boot_commit_interruption or root_restore_stream or
             boot_commit_reboot or boot_inspector_interruption)):
        raise ValueError('Root deployment stream requires a distinct disposable CONFIG transport roundtrip')
    if (type(root_restore_interruption) is not bool or
            root_restore_interruption and (not root_restore_stream or root_restore_lost_completion or
                                          boot_commit_reboot or boot_inspector_interruption)):
        raise ValueError('Restore interruption requires a distinct disposable stream/reboot trial')
    if (type(root_restore_lost_completion) is not bool or
            root_restore_lost_completion and not root_restore_stream):
        raise ValueError('Restore completion loss requires the disposable root restore stream')
    if (type(root_restore_stream) is not bool or
            (root_restore_stream and (not boot_commit_transport or boot_commit_interruption))):
        raise ValueError('Root restore stream requires a non-interrupted disposable CONFIG transport roundtrip')
    if type(root_write_claim) is not bool or (root_write_claim and not (storage and require_lease)):
        raise ValueError('Root write claim requires explicit synthetic storage and recovery lease')
    if (type(boot_inspector_interruption) is not bool or
            (boot_inspector_interruption and (not boot_commit_transport or boot_commit_interruption))):
        raise ValueError('Inspector interruption requires a non-interrupted disposable commit transport roundtrip')
    if (type(boot_commit_reboot) is not bool or
            (boot_commit_reboot and (not boot_commit_transport or boot_commit_interruption))):
        raise ValueError('Reboot qualification requires a non-interrupted disposable commit transport roundtrip')
    if type(boot_commit_interruption) is not bool or (boot_commit_interruption and not boot_commit_transport):
        raise ValueError('Commit interruption requires the disposable host transport qualification')
    if type(boot_commit_transport) is not bool or (boot_commit_transport and not boot_commit_roundtrip):
        raise ValueError('Commit transport validation requires the disposable CONFIG roundtrip')
    if type(boot_commit_roundtrip) is not bool or (boot_commit_roundtrip and not (boot_write_roundtrip and require_lease)):
        raise ValueError('CONFIG commit validation requires disposable boot writes and a recovery lease')
    if type(hash_storage) is not bool or (hash_storage and not storage):
        raise ValueError('Storage hashing requires explicit synthetic storage')
    if type(boot_write_roundtrip) is not bool or (boot_write_roundtrip and not boot_filesystem):
        raise ValueError('Disposable boot writes require explicit boot-filesystem validation')
    if type(boot_filesystem) is not bool:
        raise ValueError('Boot filesystem selection must be an explicit boolean')
    if boot_filesystem and not storage:
        raise ValueError('Boot filesystem validation requires synthetic storage')
    if backup_card and not storage:
        raise ValueError('Whole-card byte backup requires synthetic storage')
    if (type(lease_soak_seconds) is not int or
            (lease_soak_seconds != 0 and not 330 <= lease_soak_seconds <= 900) or
            (lease_soak_seconds and not require_lease)):
        raise ValueError('Lease soak requires explicit lease mode and 330..900 seconds')
    if lease_watchdog_loss and not require_lease:
        raise ValueError('Lease watchdog loss requires explicit lease validation')
    if require_lease and not require_python:
        raise ValueError('Lease validation requires Python recovery identity')
    if interrupt_backup and not backup_root:
        raise ValueError('Backup interruption requires synthetic root backup validation')
    if backup_root and not storage:
        raise ValueError('Root backup validation requires a synthetic storage fixture')
    if storage and not require_python:
        raise ValueError('Storage inspection requires the Python recovery runtime')
    if hashlib.sha256(image.read_bytes()).hexdigest() != digest:
        raise ValueError('Recovery image digest differs')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1', 0))
        port = reserve.getsockname()[1]
    public = server_key(key, server_public_key)
    known = output/'known_hosts'
    known.write_text(f'[127.0.0.1]:{port} {public}\n')
    known.chmod(0o600)
    trial_nonce = uuid.uuid4().hex
    owner = uuid.uuid4().hex + uuid.uuid4().hex
    command = [str(qemu), '-machine', 'raspi4b', '-accel', 'tcg', '-kernel', str(kernel),
               '-dtb', str(dtb), '-initrd', str(image), '-append',
               'earlycon=pl011,mmio32,0xfe201000 console=ttyAMA1,115200 rdinit=/init '
               'uconsole.recovery=1 uconsole.emulator=1 uconsole.recovery_watchdog=1 panic=10 '
               'root=/dev/ram0 uconsole.forge_trial=' + trial_nonce,
               '-display', 'none', '-monitor', 'none', '-serial', 'stdio', '-no-reboot',
               '-netdev', f'user,id=recovery,hostfwd=tcp:127.0.0.1:{port}-:2222',
               '-device', 'usb-net,netdev=recovery']
    if require_lease:
        position = command.index('-append') + 1
        command[position] += ' uconsole.recovery_lease=1 uconsole.recovery_owner=' + owner
    fixture = None
    allowed_storage_hash = None
    if storage:
        fixture = storage_fixture(output/'disposable-sd.img', boot_filesystem=boot_filesystem)
        allowed_storage_hash = fixture['sha256']
        # QEMU's SD model rejects a read-only block backend. This is exclusively
        # a newly generated fixture; checksum it after the read-only probe.
        command += ['-drive', 'if=sd,format=raw,file=' + str((output/'disposable-sd.img').absolute())]
    (output/'command.json').write_text(json.dumps(command, indent=2))
    evidence = dict(status='failed', physical_qualified=False, forced_cleanup=False, image_sha256=digest)
    process = None
    active_console=output/'console.log'
    try:
        with (output/'console.log').open('xb') as console:
            process = subprocess.Popen(command, stdout=console, stderr=subprocess.STDOUT)
            ssh = client(key, known, port)
            deadline = time.monotonic() + 100
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Recovery guest exited before SSH readiness')
                result = subprocess.run(ssh, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError('Recovery guest SSH readiness deadline')
                time.sleep(0.2)
            def execute(script, *, timeout=15, input=None):
                result = subprocess.run(ssh[:-1] + [script], input=input, capture_output=True, text=True, timeout=timeout)
                if result.returncode:
                    raise RuntimeError('Recovery command failed: ' + result.stderr[-2000:])
                return result.stdout
            mounts = execute('id -u; printf "FORGE_MOUNTS\\n"; cat /proc/mounts; '
                             'printf "FORGE_CMDLINE\\n"; cat /proc/cmdline')
            check_output(mounts)
            evidence['ram_root'] = mounts
            lease_client = None
            if require_python:
                script = ('/usr/bin/python3 -I -S -c '
                          "'import base64,ctypes,fcntl,hashlib,json,os,pathlib,shlex,ssl,subprocess,sysconfig; "
                          'ctypes.CDLL(None); '
                          'print(json.dumps(dict(uid=os.geteuid(), sha256=hashlib.sha256(b"forge").hexdigest())))' + "'")
                observed = json.loads(execute(script))
                expected = dict(uid=0, sha256=hashlib.sha256(b'forge').hexdigest())
                if observed != expected:
                    raise ValueError('Recovery Python runtime preflight differs')
                evidence['python_runtime'] = {'status': 'passed', 'isolated_no_site': True}
                expected_kernel = execute('uname -r').strip()
                probe = RecoveryProbe('127.0.0.1', key, known, trial_nonce, expected_kernel,
                                      None, port=port, mode='emulated')
                first = probe.inspect()
                bound = probe.inspect(expected_boot_id=first['verification']['boot_id'])
                evidence['ram_identity'] = bound['verification']
                evidence['ram_identity_observation'] = bound['observation']
                evidence['recovery_transport'] = {'status': 'passed', 'boot_bound_recheck': True}
                if require_lease:
                    lease_client = BackupLeaseClient(probe, bound['verification']['boot_id'], owner)
                if durable_lease:
                    evidence['durable_session'] = forge_recovery_session_probe.run(
                        probe, output/'durable-session', bound['verification']['boot_id'], owner)
                    # Explicit test-only handoff of the exact validated receipt;
                    # later watchdog checks must not start a competing sequence.
                    lease_client.accepted = evidence['durable_session']['receipt']
                if lease_soak_seconds:
                    original = json.loads(execute('cat /run/forge-lease.ready'))
                    samples = []
                    until = time.monotonic() + lease_soak_seconds
                    while True:
                        receipt = lease_client.renew()
                        sampled = float(execute('/usr/bin/python3 -I -S -c "import time; print(time.monotonic())"'))
                        samples.append(dict(receipt=receipt, guest_monotonic=sampled))
                        with (output/('lease-soak-%03d.json' % len(samples))).open('x') as stream:
                            json.dump(samples[-1], stream, indent=2)
                        if time.monotonic() >= until:
                            break
                        # Observe the same VM while waiting for the next renewal.
                        # Early process exit is a failure, never a reason to restart.
                        try:
                            process.wait(timeout=min(30, max(.01, until-time.monotonic())))
                        except subprocess.TimeoutExpired:
                            pass
                        else:
                            raise RuntimeError('Recovery exited during sustained renewal')
                    probe.inspect(expected_boot_id=bound['verification']['boot_id'])
                    if sampled <= original['deadline_monotonic'] + 20:
                        raise ValueError('Lease soak did not outlive the original guest deadline')
                    evidence['lease_soak'] = dict(status='passed', samples=samples,
                        original_deadline=original['deadline_monotonic'], boot_identity_unchanged=True)
                if storage:
                    # This qualified emulator DTB enumerates the SD host as
                    # mmc1. Device numbering is not the physical card identity.
                    cid = execute('cat /sys/class/block/mmcblk1/device/cid').strip()
                    layout = probe.inspect_storage(cid, fixture['disk_id'],
                                                   expected_boot_id=bound['verification']['boot_id'],
                                                   device='/dev/mmcblk1')
                    extent = layout['extent']
                    if (extent['mbr_sha256'] != fixture['mbr_sha256'] or
                            extent['offset_bytes'] != fixture['root_offset'] or
                            extent['length_bytes'] != fixture['root_length'] or
                            extent['disk_bytes'] != fixture['bytes']):
                        raise ValueError('Guest SD geometry differs from the generated fixture')
                    evidence['storage'] = layout
                    if root_write_claim:
                        binding=dict(nonce=trial_nonce,kernel=expected_kernel,mode='emulated',
                            device='/dev/mmcblk1',boot_id=bound['verification']['boot_id'],cid=cid,
                            disk_id=fixture['disk_id'],extent=extent)
                        guards=dict(root=dict(offset=fixture['root_offset'],bytes=fixture['root_length'],
                                              sha256=fixture['root_sha256']),
                                    prefix=dict(offset=0,bytes=fixture['root_offset'],sha256=fixture['protected_prefix_sha256']),
                                    suffix=dict(offset=fixture['bytes'],bytes=0,sha256=hashlib.sha256(b'').hexdigest()))
                        source=restore_claim_script(binding,guards)
                        with (output/'root-write-claim-worker.py').open('x') as retained:
                            os.fchmod(retained.fileno(),0o600)
                            retained.write(source)
                        lease_client.renew()
                        result=json.loads(execute('/usr/bin/python3 -I -S',input=source,timeout=180))
                        expected=dict(status='passed',root_written=True,root_restored=True,
                            competing_read_claim_rejected=True,competing_write_claim_rejected=True,
                            protected_ranges_unchanged=True,physical_qualified=False)
                        if result!=expected: raise ValueError('Writable root claim receipt differs')
                        probe.inspect(expected_boot_id=binding['boot_id'])
                        verify_storage(output/'disposable-sd.img',fixture,expected_sha256=allowed_storage_hash,
                                       root_write_roundtrip=True)
                        evidence['root_write_claim']=result
                    if boot_filesystem:
                        source = bootfs_script(trial_nonce, bound['verification']['boot_id'], expected_kernel, cid,
                                               write_roundtrip=boot_write_roundtrip)
                        if boot_write_roundtrip:
                            guards = dict(
                                root=dict(offset=fixture['root_offset'], bytes=fixture['root_length'],
                                          sha256=fixture['root_sha256']),
                                prefix=dict(offset=0, bytes=fixture['root_offset'],
                                            sha256=fixture['protected_prefix_sha256']),
                                suffix=dict(offset=fixture['bytes'], bytes=0,
                                            sha256=hashlib.sha256(b'').hexdigest()))
                            source = claim_probe_script(source, dict(nonce=trial_nonce,
                                boot_id=bound['verification']['boot_id'], kernel=expected_kernel,
                                cid=cid, disk_id=fixture['disk_id'], extent=extent), guards)
                        with (output/'boot-filesystem-worker.py').open('x') as retained:
                            os.fchmod(retained.fileno(), 0o600)
                            retained.write(source)
                        # The claimed-root variant hashes 188 MiB under TCG;
                        # keep its bound below the initial recovery lease.
                        mounted = json.loads(execute('/usr/bin/python3 -I -S -c '+shlex.quote(source),
                                                     timeout=120 if boot_write_roundtrip else 15))
                        expected = dict(status='passed', filesystem='vfat', read_only=not boot_write_roundtrip,
                                        private_mount=True, unmounted=True,
                                        physical_qualified=False, root_written=False)
                        if boot_write_roundtrip:
                            expected.update(roundtrip_checks=FAT_CHECKS, files_restored_to_absence=True)
                            expected.update(root_claim_qualified=True, competing_root_claim_rejected=True)
                        else:
                            expected['write_rejected'] = True
                        if mounted != expected:
                            raise ValueError('Boot filesystem probe acknowledgement differs')
                        probe.inspect(expected_boot_id=bound['verification']['boot_id'])
                        evidence['boot_filesystem'] = mounted
                        observed = verify_storage(output/'disposable-sd.img', fixture,
                                                  allow_boot_changes=boot_write_roundtrip)
                        if boot_write_roundtrip and observed['unchanged']:
                            raise ValueError('Boot-write trial left no independent storage-change evidence')
                        allowed_storage_hash = observed['sha256']
                        evidence['storage_after_boot_probe'] = observed
                    if boot_commit_roundtrip:
                        review, image_data = forge_bootcommit_probe.fixture(trial_nonce)
                        binding = dict(nonce=trial_nonce,kernel=expected_kernel,serial=None,mode='emulated',
                            boot_id=bound['verification']['boot_id'],cid=cid,disk_id=fixture['disk_id'],
                            device='/dev/mmcblk1',extent=extent)
                        root_guard = dict(offset=fixture['root_offset'],bytes=fixture['root_length'],
                                          sha256=fixture['root_sha256'])
                        def commit_worker(request, name):
                            source = forge_bootcommit_probe.script(request)
                            with (output/(name+'-worker.py')).open('x') as retained:
                                os.fchmod(retained.fileno(),0o600)
                                retained.write(source)
                            lease_client.renew()
                            # Stream owner code over stdin, not argv or logs.
                            return json.loads(execute('/usr/bin/python3 -I -S', input=source, timeout=180))
                        seeded = commit_worker(dict(phase='seed',binding=binding,review=review,
                            image_data=image_data,token=uuid.uuid4().hex,root_guard=root_guard), 'config-seed')
                        if seeded != dict(status='seeded-disposable-boot',root_written=False):
                            raise ValueError('Disposable CONFIG seed acknowledgement differs')
                        commits = []
                        for operation in ('install-hold','release-hold'):
                            host = verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True)
                            plan, pin = forge_bootcommit_probe.transition(review,binding,host,root_guard,operation)
                            with (output/(operation+'-plan.json')).open('x') as retained:
                                os.fchmod(retained.fileno(),0o600)
                                retained.write(json.dumps(plan,sort_keys=True,indent=2)+'\n')
                            if boot_commit_transport:
                                journal=output/(operation+'-journal')
                                journal.mkdir(mode=0o700)
                                journal_fd=private_directory(journal)
                                try:
                                    if write_record(journal_fd,'plan.json',plan)!=pin:
                                        raise ValueError('Disposable CONFIG journal pin differs')
                                finally: os.close(journal_fd)
                                authorize=lambda reviewed:dict(commit=pin,boot_id=binding['boot_id'])
                                if boot_commit_interruption:
                                    fault='kill-after-write' if operation=='install-hold' else 'lose-completion'
                                    failed=forge_commit_fault_probe.dispatch(probe,journal,pin,lease_client,
                                                                             authorize=authorize,fault=fault)
                                    reconciled=reconcile_boot_commit(probe,journal,pin,lease_client)
                                    expected_fence='incomplete' if operation=='install-hold' else 'completed'
                                    if (reconciled['status']!='reconciled-after' or
                                            reconciled['fence']['outcome']['status']!=expected_fence or
                                            reconciled['fence']['stale_boot_unmounted']!=(operation=='install-hold')):
                                        raise ValueError('Interrupted CONFIG reconciliation differs from injected failure')
                                    evidence.setdefault('boot_commit_interruptions',[]).append(
                                        dict(failed,operation=operation,reconciliation=reconciled))
                                    # Keep the original attempt uncertain; do not fabricate a
                                    # successful commit acknowledgement from later observations.
                                    continue
                                else:
                                    accepted=dispatch_boot_commit(probe,journal,pin,lease_client,authorize=authorize)
                                    receipt=accepted['receipt']
                            else:
                                receipt = commit_worker(dict(phase='commit',binding=binding,plan=plan,pin=pin,
                                                            owner=owner),operation)
                            expected = dict(status='boot-file-commit-verified',plan_sha256=pin,
                                boot_id=binding['boot_id'],operation=operation,
                                file=dict(status='applied',path='/boot/firmware/config.txt'),
                                root_written=False,boot_unmounted=True,reboot_performed=False,
                                physical_boot_qualified=False)
                            if receipt != expected:
                                raise ValueError('Disposable CONFIG commit acknowledgement differs')
                            commits.append(receipt)
                            def reboot_restore():
                                nonlocal process,active_console,bound,binding,lease_client
                                old_boot=binding['boot_id']
                                process,active_console,reboot=forge_recovery_reboot_probe.restart(
                                    process,command,ssh,probe,old_boot,output,active_console)
                                bound=forge_recovery_reboot_probe.ready(process,probe,old_boot)
                                binding=dict(binding,boot_id=bound['verification']['boot_id'])
                                lease_client=BackupLeaseClient(probe,binding['boot_id'],owner)
                                return dict(binding,lease_owner=owner),lease_client,reboot
                            if root_deploy_stream and operation=='install-hold':
                                held=reconcile_boot_commit(probe,journal,pin,lease_client)
                                if held['status']!='reconciled-after':
                                    raise ValueError('Disposable hold did not reconcile before deployment')
                                evidence['root_deploy_stream']=forge_deploy_stream_probe.run(
                                    probe,output/'root-deploy-stream',dict(binding,lease_owner=owner),
                                    plan,pin,held['inspection'],lease_client,
                                    lost_completion=root_deploy_lost_completion,
                                    reboot=reboot_restore if root_deploy_interruption else None)
                            if root_restore_stream and operation=='install-hold':
                                held=reconcile_boot_commit(probe,journal,pin,lease_client)
                                if held['status']!='reconciled-after':
                                    raise ValueError('Disposable persistent hold did not reconcile before root restore')
                                evidence['root_restore_stream']=forge_restore_stream_probe.run(
                                    probe,output/'root-restore-stream',dict(binding,lease_owner=owner),
                                    plan,pin,held['inspection'],lease_client,
                                    lost_completion=root_restore_lost_completion,
                                    reboot=reboot_restore if root_restore_interruption else None)
                            if boot_inspector_interruption and operation=='install-hold':
                                initial=verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True)
                                interrupted=forge_commit_fault_probe.interrupt_inspector(probe,journal,pin,lease_client)
                                reconciled=reconcile_boot_commit(probe,journal,pin,lease_client)
                                if reconciled['status']!='reconciled-after':
                                    raise ValueError('Fresh observation after inspector hangup did not reconcile')
                                verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True,
                                               expected_sha256=initial['sha256'])
                                evidence['boot_inspector_interruption']=dict(interrupted,
                                    fresh_reconciliation=reconciled,card_unchanged=True)
                            if boot_commit_reboot and operation=='install-hold':
                                old_boot=binding['boot_id']
                                old_card=verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True)
                                process,active_console,reboot=forge_recovery_reboot_probe.restart(
                                    process,command,ssh,probe,old_boot,output,active_console)
                                bound=forge_recovery_reboot_probe.ready(process,probe,old_boot)
                                new_boot=bound['verification']['boot_id']
                                lease_client=BackupLeaseClient(probe,new_boot,owner)
                                reconciled=reconcile_boot_commit(probe,journal,pin,lease_client,observed_boot_id=new_boot)
                                if (reconciled['status']!='reconciled-after' or
                                        reconciled['fence']['outcome']['status']!='previous-boot-ended'):
                                    raise ValueError('Reboot-aware commit observation differs')
                                stale=forge_recovery_reboot_probe.reject_old_worker(probe,journal,pin,lease_client,output)
                                verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True,
                                               expected_sha256=old_card['sha256'])
                                binding=dict(binding,boot_id=new_boot)
                                evidence['boot_commit_reboot']=dict(reboot,current_boot_id=new_boot,
                                    reconciliation=reconciled,stale_worker=stale,card_unchanged=True)
                        observed = verify_storage(output/'disposable-sd.img',fixture,allow_boot_changes=True)
                        allowed_storage_hash = observed['sha256']
                        evidence['boot_commits'] = commits
                        evidence['boot_commit_transport'] = boot_commit_transport
                        evidence['storage_after_boot_commits'] = observed
                    if backup_root:
                        partial_hashes = None
                        if interrupt_backup:
                            partial = output/'incomplete-root-backup'
                            try:
                                backup_partition(probe,partial,cid,fixture['disk_id'],
                                                 boot_id=bound['verification']['boot_id'],device='/dev/mmcblk1',
                                                 compressed_limit=4096, lease=lease_client)
                            except ValueError as exc:
                                if 'compressed size bound' not in str(exc): raise
                            else:
                                raise ValueError('Deliberately undersized backup unexpectedly completed')
                            rejected = json.loads((partial/'acceptance.json').read_text())
                            if rejected['status']!='incomplete' or rejected['restore_authorized']:
                                raise ValueError('Interrupted backup was accepted as usable')
                            partial_hashes = {name:hashlib.sha256((partial/name).read_bytes()).hexdigest()
                                              for name in ('plan.json','acceptance.json','root.img.gz')}
                            # Observe release of this read-only worker's exclusive
                            # partition claim; never kill an unknown guest process.
                            claim = ('/usr/bin/python3 -I -S -c '
                                     "'import os; fd=os.open(\"/dev/mmcblk1p2\",os.O_RDONLY|os.O_EXCL|os.O_NOFOLLOW); os.close(fd)'")
                            deadline = time.monotonic()+10
                            while True:
                                checked = subprocess.run(ssh[:-1]+[claim],capture_output=True,text=True,timeout=15)
                                if checked.returncode==0: break
                                if time.monotonic()>=deadline:
                                    raise RuntimeError('Interrupted backup retained its partition claim')
                                time.sleep(0.2)
                            probe.inspect(expected_boot_id=bound['verification']['boot_id'])
                        result = backup_partition(probe,output/'root-backup',cid,fixture['disk_id'],
                                                  boot_id=bound['verification']['boot_id'],device='/dev/mmcblk1',
                                                  lease=lease_client)
                        if require_lease:
                            plan = json.loads((output/'root-backup/plan.json').read_text())
                            if plan.get('lease_owner') != owner or plan.get('transfer_timeout_seconds') != 82800:
                                raise ValueError('Backup did not use the bound renewable lease')
                            evidence['backup_lease'] = dict(owner=owner, accepted=lease_client.accepted)
                        expected_hash = hashlib.sha256()
                        with (output/'disposable-sd.img').open('rb') as source:
                            source.seek(fixture['root_offset'])
                            remaining = fixture['root_length']
                            while remaining:
                                data = source.read(min(1048576,remaining))
                                if not data: raise ValueError('Synthetic root fixture truncated')
                                expected_hash.update(data)
                                remaining -= len(data)
                        if result['root'] != dict(bytes=fixture['root_length'],sha256=expected_hash.hexdigest()):
                            raise ValueError('Backup differs from independent host root-fixture bytes')
                        evidence['root_backup'] = result
                        if partial_hashes is not None:
                            for name,partial_digest in partial_hashes.items():
                                if hashlib.sha256((partial/name).read_bytes()).hexdigest()!=partial_digest:
                                    raise ValueError('Fresh backup changed prior interruption evidence')
                            evidence['backup_interruption'] = {'status':'passed','incomplete_retained':True,
                                                              'partition_claim_released':True,
                                                              'fresh_backup_verified':True}
                    if backup_card:
                        card = backup_partition(probe, output/'card-backup', cid, fixture['disk_id'],
                            boot_id=bound['verification']['boot_id'], device='/dev/mmcblk1',
                            lease=lease_client, whole_card=True)
                        if card['card'] != dict(bytes=fixture['bytes'], sha256=allowed_storage_hash):
                            raise ValueError('Whole-card backup differs from independent fixture hash')
                        evidence['card_backup'] = card
                    if hash_storage:
                        hashed = capture_storage_hash(probe, output/'storage-hashes', cid, fixture['disk_id'],
                            boot_id=bound['verification']['boot_id'], device='/dev/mmcblk1', lease=lease_client)
                        host = storage_digests(output/'disposable-sd.img', fixture['boot_offset'], fixture['root_offset'])
                        expected = dict(boot_id=bound['verification']['boot_id'],
                            card=dict(bytes=fixture['bytes'], sha256=allowed_storage_hash),
                            prefix=dict(offset=0, bytes=fixture['root_offset'], sha256=host['protected_prefix_sha256']),
                            root=dict(offset=fixture['root_offset'], bytes=fixture['root_length'], sha256=host['root_sha256']),
                            suffix=dict(offset=fixture['bytes'], bytes=0, sha256=hashlib.sha256(b'').hexdigest()))
                        if host['sha256'] != allowed_storage_hash or hashed['digests'] != expected:
                            raise ValueError('Offline card hashes differ from independent host ranges')
                        evidence['storage_hashes'] = hashed
            if require_network_observer:
                evidence['network_observer'] = network_samples(execute('cat /run/forge-network-status.log'))
            if require_lease:
                ready = json.loads(execute('cat /run/forge-lease.ready'))
                if (ready['nonce'] != trial_nonce or ready['owner'] != owner or
                        ready['boot_id'] != bound['verification']['boot_id'] or ready['sequence'] != 0):
                    raise ValueError('Lease readiness is not bound to this guest')
                request = dict(schema=1, purpose='offline-backup', nonce=trial_nonce, owner=owner,
                               boot_id=bound['verification']['boot_id'],
                               sequence=1 if lease_client.accepted is None else lease_client.accepted['sequence']+1,
                               seconds=300)
                source = ('import sys,json,time; sys.path.insert(0,"/etc/forge"); '
                          'from forge_recovery_lease_socket import exchange; '
                          'print(json.dumps(dict(receipt=exchange("/run/forge-lease/lease.sock",'
                          + repr(request) + '), now=time.monotonic())))')
                remote = '/usr/bin/python3 -I -S -c ' + shlex.quote(source)
                receipt = lease_client.renew()
                retry = json.loads(execute(remote))
                if (receipt != retry['receipt'] or receipt['sequence'] != request['sequence'] or
                        receipt['deadline_monotonic'] <= ready['deadline_monotonic'] or
                        receipt['root_write_authorized'] or receipt['normal_boot_release_authorized']):
                    raise ValueError('Lease renewal or non-extending retry differs')
                remaining = receipt['deadline_monotonic'] - retry['now']
                if not 0 < remaining <= 300:
                    raise ValueError('Lease expiry is not bounded')
                evidence['lease'] = dict(initial=ready, renewed=receipt, retry_unchanged=True,
                                         remaining_seconds=remaining)
                if lease_watchdog_loss:
                    started = time.monotonic()
                    evidence['lease']['failure_injection'] = json.loads(execute(
                        '/usr/bin/python3 -I -S -c ' + shlex.quote(LEASE_FAULT)))
                    process.wait(timeout=40)
                    elapsed = time.monotonic() - started
                    verify_expiry(process.returncode, elapsed,
                                  active_console.read_text(errors='replace'))
                    if storage:
                        evidence['storage_fixture'] = verify_storage(output/'disposable-sd.img', fixture,
                            allow_boot_changes=boot_write_roundtrip, expected_sha256=allowed_storage_hash,
                            root_write_roundtrip=root_write_claim or root_restore_stream or root_deploy_stream)
                    evidence['lease'].update(status='modeled-watchdog-expiry-passed',
                                             keeper_loss_to_exit_seconds=elapsed,
                                             physical_qualified=False)
                    evidence['status'] = 'passed'
                    return evidence
                # Stop renewing, but do not kill either timer. A software reboot
                # here is lease-loss evidence, never hardware-watchdog evidence.
                started = time.monotonic()
                process.wait(timeout=remaining + 120)
                elapsed = time.monotonic() - started
                console_text = active_console.read_text(errors='replace')
                verify_lease_expiry(process.returncode, elapsed, remaining, console_text)
                evidence['lease'].update(status='passed', no_renewal_to_exit_seconds=elapsed,
                                         hardware_expiry_qualified=False)
                if storage:
                    evidence['storage_fixture'] = verify_storage(output/'disposable-sd.img', fixture,
                        allow_boot_changes=boot_write_roundtrip, expected_sha256=allowed_storage_hash,
                        root_write_roundtrip=root_write_claim or root_restore_stream or root_deploy_stream)
                evidence['status'] = 'passed'
                return evidence
            state = execute('cat /run/forge-watchdog.ready; cat /sys/class/watchdog/watchdog0/state; '
                            'cat /sys/class/watchdog/watchdog0/timeout')
            evidence['watchdog'] = state
            import re
            if not re.fullmatch(r'pid=[1-9][0-9]* timeout=15 maximum_lifetime=300\nactive\n15\n', state):
                raise ValueError('Guest watchdog ownership/readback differs')
            pid = state.split()[0].split('=')[1]
            # Guard the exact disposable guest process before failure injection.
            script = f'test "$(readlink /proc/{pid}/exe)" = /usr/sbin/forge-watchdog && kill -KILL {pid}'
            started = time.monotonic()
            execute(script)
            process.wait(timeout=40)
            elapsed = time.monotonic() - started
            evidence.update(keeper_loss_to_exit_seconds=elapsed, qemu_exit=process.returncode)
            text = active_console.read_text(errors='replace')
            verify_expiry(process.returncode, elapsed, text)
            if storage:
                evidence['storage_fixture'] = verify_storage(output/'disposable-sd.img', fixture,
                    allow_boot_changes=boot_write_roundtrip, expected_sha256=allowed_storage_hash,
                    root_write_roundtrip=root_write_claim or root_restore_stream or root_deploy_stream)
            evidence['status'] = 'passed'
    except BaseException as exc:
        evidence['error'] = str(exc)
        raise
    finally:
        if process is not None and process.poll() is None:
            evidence['forced_cleanup'] = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        (output/'acceptance.json').write_text(json.dumps(evidence, indent=2))
    return evidence


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('qemu', 'kernel', 'dtb', 'image', 'key', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--require-python', action='store_true')
    parser.add_argument('--require-network-observer', action='store_true')
    parser.add_argument('--require-lease', action='store_true')
    parser.add_argument('--durable-lease', action='store_true', help='Qualify durable session restart and explicit lost-reply retry on the disposable VM')
    parser.add_argument('--lease-watchdog-loss', action='store_true')
    parser.add_argument('--lease-soak-seconds', type=int, default=0)
    parser.add_argument('--server-public-key', type=Path,
                        help='Pinned server public key when distinct from the disposable client key')
    parser.add_argument('--storage', action='store_true', help='Read-only probe of a new synthetic SD image')
    parser.add_argument('--root-write-claim', action='store_true', help='Write/restore one sector under an exclusive claim on the synthetic SD root only')
    parser.add_argument('--root-restore-stream', action='store_true', help='Qualify streamed backup restoration after a controlled disposable root change')
    parser.add_argument('--root-deploy-stream', action='store_true', help='Deploy a clean root derivative to the synthetic card, then restore the original backup')
    parser.add_argument('--root-deploy-lost-completion', action='store_true', help='Lose the derivative completion reply and reconcile without retrying deployment')
    parser.add_argument('--root-deploy-interruption', action='store_true', help='Interrupt a partial derivative, prove same-boot refusal, then roll back in a new disposable recovery boot')
    parser.add_argument('--root-restore-lost-completion', action='store_true', help='Lose the restore reply, then fence and hash without retrying writes')
    parser.add_argument('--root-restore-interruption', action='store_true', help='Kill a partial restore, prove same-boot fencing, then restore in a fresh disposable boot')
    parser.add_argument('--boot-filesystem', action='store_true', help='Read-only mount and write-denial test on synthetic FAT32')
    parser.add_argument('--boot-write-roundtrip', action='store_true', help='Apply/restore disposable FAT files; verify protected SD ranges')
    parser.add_argument('--boot-commit-roundtrip', action='store_true', help='Guarded CONFIG install/release on synthetic FAT only')
    parser.add_argument('--boot-commit-transport', action='store_true', help='Qualify the separate pinned two-phase host commit channel')
    parser.add_argument('--boot-commit-interruption', action='store_true', help='Fence/inspect SIGKILL and lost-completion cases on synthetic storage only')
    parser.add_argument('--boot-commit-reboot', action='store_true', help='Reboot the synthetic card between CONFIG install/release and reject an old worker')
    parser.add_argument('--boot-inspector-interruption', action='store_true', help='Hang up a mounted read-only inspector and verify cleanup and fresh reconciliation')
    parser.add_argument('--hash-storage', action='store_true', help='Verify offline range digests without another image copy')
    parser.add_argument('--backup-root', action='store_true', help='Back up only the synthetic root partition')
    parser.add_argument('--backup-card', action='store_true', help='Read-only byte backup of the entire synthetic card')
    parser.add_argument('--interrupt-backup', action='store_true', help='Test a size-capped failure before fresh backup')
    print(json.dumps(run(**vars(parser.parse_args())), indent=2))
