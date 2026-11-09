"""Installed final-mount observer and one-shot normal reboot worker."""
import json
import os
import subprocess
import sys

from forge_boot_artifacts import inventory
from forge_boot_observation import read_boot, normal_boot, verify
from forge_boot_privacy import MOUNT_CHECK, verify_mount
from forge_target_files import parent_fd, snapshot, equivalent
from forge_target_ssh import target_lock


def run(request):
    if request['operation'] not in ('reboot', 'inspect') or os.geteuid() != 0:
        raise ValueError('Expected installed root final-mount operation')
    frozen = request['frozen']
    clean, plan = frozen['cleanup'], frozen['plan']
    reboot = request['operation'] == 'reboot'
    with target_lock('/run/lock/uconsole-forge-target.lock'):
        boot = normal_boot(read_boot(), clean['machine_id'])
        if reboot:
            if boot['boot_id'] != clean['boot_id']: raise ValueError('Cleanup boot changed; inspect instead of rebooting')
        else:
            verify(boot, dict(clean['original_boot'], boot_id=clean['boot_id']), clean['machine_id'])
        roots = lambda text: [v for v in text.split() if v.startswith('root=')]
        if roots(boot['cmdline']) != roots(clean['original_boot']['cmdline']): raise ValueError('Native root differs')
        def files():
            desired = [plan['before']['files'][0]]
            for value in clean['before']['files']:
                # FAT mask restoration changes the visible mode, not file bytes.
                desired.append(dict(value, mode=0o755) if not reboot and value['kind'] == 'file' else value)
            for wanted in desired:
                with parent_fd(wanted['path']) as (fd, name):
                    if not equivalent(snapshot(fd, name, wanted['path']), wanted):
                        raise ValueError('Original fstab or boot preimages changed')
        files()
        mount = json.loads(subprocess.check_output([sys.executable, '-c', MOUNT_CHECK],
            stdin=subprocess.DEVNULL, text=True, timeout=30))
        checked = verify_mount(mount, clean['image_plan']['boot_source'], reboot)
        current = inventory('/boot/firmware', [clean['image_plan']['sha256']])
        if current != frozen['inventory']: raise ValueError('Boot inventory changed since guarded fstab restoration')
        files()
        if read_boot() != boot: raise ValueError('Boot changed during final mount observation')
        if reboot:
            subprocess.run(['/usr/bin/systemctl', 'reboot'], stdin=subprocess.DEVNULL, check=True, timeout=15)
            return dict(reboot_requested=True, reboot_observed=False)
        return dict(boot=boot, mount=mount, verification=checked, inventory=current,
                    mutation_performed=False, reboot_requested=False)
