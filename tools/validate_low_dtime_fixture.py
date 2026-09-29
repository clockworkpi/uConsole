"""Reproduce an ext4 low-dtime warning on disposable host files only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from forge_target_journal import private_directory, write_record


def digest(path):
    hashed = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(1048576):
            hashed.update(data)
    return hashed.hexdigest()


def copy_new(source, destination):
    with source.open('rb') as incoming, destination.open('xb') as outgoing:
        os.fchmod(outgoing.fileno(), 0o600)
        shutil.copyfileobj(incoming, outgoing, 1048576)
        outgoing.flush()
        os.fsync(outgoing.fileno())


def validate(destination):
    destination = Path(destination).absolute()
    destination.mkdir(mode=0o700)
    fd = private_directory(destination)
    result = dict(status='incomplete', physical_qualified=False, target_written=False)
    events = []
    def run(argv):
        process = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                                 env=dict(os.environ, LC_ALL='C'))
        record = dict(argv=[str(arg) for arg in argv], returncode=process.returncode,
                      stdout=process.stdout, stderr=process.stderr)
        write_record(fd, 'command-%02d.json' % len(events), record)
        events.append(record)
        return record
    try:
        original = destination/'low-dtime.img'
        with original.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.truncate(32*1024*1024)
        assert run(['mke2fs', '-q', '-t', 'ext4', '-F', str(original)])['returncode'] == 0
        assert run(['e2fsck', '-f', '-n', str(original)])['returncode'] == 0
        created = run(['debugfs', '-w', '-R', 'write /dev/null /fixture', str(original)])
        number = int(re.search(r'Allocated inode: (\d+)', created['stdout'])[1])
        run(['debugfs', '-w', '-R', 'rm /fixture', str(original)])
        run(['debugfs', '-w', '-R', 'set_inode_field <'+str(number)+'> dtime 4', str(original)])
        stat = run(['debugfs', '-R', 'stat <'+str(number)+'>', str(original)])
        bitmap = run(['debugfs', '-R', 'testi <'+str(number)+'>', str(original)])
        assert 'Links: 0' in stat['stdout'] and 'Blockcount: 0' in stat['stdout']
        assert 'Inode '+str(number)+' is not in use' in bitmap['stdout']
        baseline = digest(original)
        warning = run(['e2fsck', '-f', '-n', str(original)])
        assert warning['returncode'] == 4
        assert 'Inode '+str(number)+' was part of the orphaned inode list' in warning['stdout']
        repaired = destination/'repaired.img'
        copy_new(original, repaired)
        undo = destination/'repair.e2undo'
        corrected = run(['e2fsck', '-f', '-y', '-z', str(undo), str(repaired)])
        assert corrected['returncode'] == 1
        assert run(['e2fsck', '-f', '-n', str(repaired)])['returncode'] == 0
        replay = destination/'undo-replay.img'
        copy_new(repaired, replay)
        assert run(['e2undo', str(undo), str(replay)])['returncode'] == 0
        assert digest(replay) == baseline
        assert digest(original) == baseline
        result.update(status='passed', warning_reproduced=True, repaired_copy_checked=True,
                      undo_restored_exact_bytes=True, original_preserved=True,
                      baseline_sha256=baseline, repaired_sha256=digest(repaired),
                      fixture_inode=number)
    except BaseException as exc:
        result['error'] = type(exc).__name__+': '+str(exc)
        raise
    finally:
        write_record(fd, 'acceptance.json', result)
        os.close(fd)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(validate(args.output), indent=2))
