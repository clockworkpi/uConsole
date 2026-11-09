"""Create editable user projects from immutable installed source bundles."""
import json
from pathlib import Path
import shutil
import tempfile

from forge_workspace import WorkspaceLock, sha256, write_json, sync_directory, sync_file


def source_manifest(source):
    files = {}
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Bundled source contains an unsupported symlink: {path}')
        if path.is_file():
            files[str(path.relative_to(source))] = sha256(path)
    return files


def keyboard_project(root, build_root):
    """Return (editable sketch path, bundled-source-update-available).

    Source checkouts keep their existing edit-in-place behavior. Installed
    bundles are copied once, even when writable, so upgrades cannot erase the
    user's project. Existing projects are never merged or overwritten silently.
    """
    root, build_root = Path(root).resolve(), Path(build_root).resolve()
    source = root / 'Code/uconsole_keyboard'
    sketch = source / 'uconsole_keyboard.ino'
    if not sketch.is_file():
        return None, False
    if (root / '.git').exists():
        return sketch, False
    projects = build_root / 'projects'
    projects.mkdir(parents=True, exist_ok=True)
    target = projects / 'uconsole_keyboard'
    current = source_manifest(source)
    with WorkspaceLock(projects):
        if target.is_symlink():
            raise ValueError('Editable keyboard project must not be a symlink')
        if target.exists():
            marker = target / '.forge-origin.json'
            entry = target / 'uconsole_keyboard.ino'
            if not marker.is_file() or not entry.is_file() or entry.is_symlink():
                raise ValueError(f'Existing keyboard project is incomplete; preserve/recover it before retrying: {target}')
            origin = json.loads(marker.read_text())
            return entry, origin.get('files') != current
        with tempfile.TemporaryDirectory(prefix='.keyboard-project-', dir=projects) as directory:
            stage = Path(directory) / 'uconsole_keyboard'
            shutil.copytree(source, stage)
            # Package modes may be read-only. User projects must remain editable.
            stage.chmod(0o755)
            for path in stage.rglob('*'):
                path.chmod(0o755 if path.is_dir() else 0o644 | (path.stat().st_mode & 0o111))
                if path.is_file():
                    sync_file(path)
            write_json(stage / '.forge-origin.json', {'schema': 1, 'source': str(source), 'files': current})
            stage.rename(target)
            sync_directory(projects)
    return target / 'uconsole_keyboard.ino', False
