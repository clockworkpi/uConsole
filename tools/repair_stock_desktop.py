#!/usr/bin/env python3
"""Preview/repair the stock image's stale cpi Desktop override.

Run inside the guest with --root /, or against a trusted offline root tree.
Stop desktop sessions before --apply; a running PCManFM may save stale settings.
This is an explicit shared-image repair, not part of emulator boot adaptation.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


STOCK = b'''[*]
desktop_bg=#202020
desktop_shadow=#202020
desktop_fg=#E8E8E8
desktop_font=Sans 20
wallpaper=/usr/share/rpd-wallpaper/sunrise.jpg
wallpaper_mode=color
show_documents=1
show_trash=0
show_mounts=1
folder=/home/cpi/Desktop
'''
REPAIRED = STOCK.replace(b'folder=/home/cpi/Desktop\n', b'')
BACKUP_SUFFIX = '.uconsole-original'


def safe_path(root, relative):
    """Do not follow symlinks, including guest-absolute links in offline roots."""
    path = root
    for component in relative.parts:
        path = path / component
        if path.is_symlink():
            return None
    return path


def repair(root, apply=False):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Guest root must be a directory')
    # A real cpi home can make the stock override intentional. Fail closed even
    # for a dangling symlink rather than interpreting paths outside the tree.
    cpi = root / 'home/cpi'
    if cpi.exists() or cpi.is_symlink():
        return [{'status': 'skipped', 'reason': 'cpi home exists'}]
    homes = [Path('etc/skel')]
    directory = safe_path(root, Path('home'))
    if directory and directory.is_dir():
        homes += [Path('home') / p.name for p in sorted(directory.iterdir())
                  if not p.is_symlink() and p.is_dir()]
    records = []
    for home in homes:
        for name in ('desktop-items-0.conf', 'desktop-items-DSI-1.conf'):
            relative = home / '.config/pcmanfm/LXDE-pi' / name
            path = safe_path(root, relative)
            if path is None or not path.exists():
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != len(STOCK):
                continue
            if path.read_bytes() != STOCK:
                continue
            record = {'path': str(relative), 'status': 'eligible'}
            records.append(record)
            if not apply:
                continue
            backup = path.with_name(path.name + BACKUP_SUFFIX)
            # Never overwrite a previous backup, including a dangling symlink.
            if backup.exists() or backup.is_symlink():
                record.update(status='skipped', reason='backup already exists')
                continue
            with backup.open('xb') as output:
                os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
                if os.geteuid() == 0:
                    os.fchown(output.fileno(), info.st_uid, info.st_gid)
                output.write(STOCK)
                output.flush()
                os.fsync(output.fileno())
            descriptor, temporary = tempfile.mkstemp(prefix='.uconsole-desktop-', dir=path.parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    os.fchmod(output.fileno(), stat.S_IMODE(info.st_mode))
                    if os.geteuid() == 0:
                        os.fchown(output.fileno(), info.st_uid, info.st_gid)
                    output.write(REPAIRED)
                    output.flush()
                    os.fsync(output.fileno())
                # Refuse intervening edits rather than replacing their content.
                current = path.stat()
                identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                          value.st_mtime_ns, value.st_ctime_ns)
                if path.is_symlink() or identity(current) != identity(info) or path.read_bytes() != STOCK:
                    raise ValueError(f'Configuration changed during repair: {relative}')
                os.replace(temporary, path)
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            record.update(status='repaired', backup=str(backup.relative_to(root)))
    return records


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--root', type=Path, required=True)
    cli.add_argument('--apply', action='store_true', help='write repairs and exclusive original backups')
    options = cli.parse_args()
    print(json.dumps(repair(options.root, options.apply), indent=2))


if __name__ == '__main__':
    main()
