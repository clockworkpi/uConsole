#!/usr/bin/env python3
"""Stage a local Trixie Quickshell package from the tested source build."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REVISION = '1a4716cde794a59928d9d9fc15f2afc7a95de360'


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if run('git', '-c', 'safe.directory=' + str(source), '-C', str(source), 'rev-parse', 'HEAD', capture_output=True).stdout.strip() != REVISION:
        parser.error('source must be the documented Quickshell revision')
    if 'VERSION_CODENAME=trixie' not in Path('/etc/os-release').read_text():
        parser.error('build this package in Debian Trixie')
    run('cmake', '--build', str(source / 'build'), '-j4')
    run('xvfb-run', '-a', 'ctest', '--test-dir', str(source / 'build'), '--output-on-failure', '--no-tests=error',
        env={**os.environ, 'LANG': 'C.UTF-8'})
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='quickshell-package-') as temporary:
        work = Path(temporary)
        stage = work / 'stage'
        def install(origin, relative, mode=0o644):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / origin, target)
            target.chmod(mode)
        install('build/src/quickshell', 'usr/bin/quickshell', 0o755)
        run('strip', '--strip-unneeded', str(stage / 'usr/bin/quickshell'))
        install('assets/org.quickshell.desktop', 'usr/share/applications/org.quickshell.desktop')
        install('assets/quickshell.svg', 'usr/share/icons/hicolor/scalable/apps/org.quickshell.svg')
        licenses = {
            'LICENSE': 'LICENSE', 'LICENSE-GPL': 'LICENSE-GPL',
            'build/_deps/cpptrace-src/LICENSE': 'cpptrace-LICENSE',
            'build/_deps/libdwarf-src/COPYING': 'libdwarf-COPYING',
            'build/_deps/libdwarf-src/src/lib/libdwarf/LGPL.txt': 'libdwarf-LGPL.txt',
            'build/_deps/libdwarf-src/src/lib/libdwarf/LIBDWARFCOPYRIGHT': 'LIBDWARFCOPYRIGHT',
            'build/_deps/zstd-src/LICENSE': 'zstd-LICENSE',
        }
        for origin, name in licenses.items():
            install(origin, 'usr/share/doc/quickshell/' + name)
        (stage / 'usr/share/doc/quickshell/copyright').write_text(
            'Quickshell source: https://github.com/quickshell-mirror/quickshell\n'
            f'Revision: {REVISION}\n'
            'Quickshell: GNU LGPL 3; see LICENSE and LICENSE-GPL.\n'
            'Statically linked cpptrace, libdwarf-lite and zstd notices are included separately.\n'
            'Local build for evaluation; vendor publication and hardware acceptance are pending.\n')
        (work / 'debian').mkdir()
        (work / 'debian/control').write_text('Source: quickshell\n\nPackage: quickshell\nArchitecture: any\nDescription: Quickshell local build\n')
        dependencies = run('dpkg-shlibdeps', '-O', '-e' + str(stage / 'usr/bin/quickshell'),
                           cwd=work, capture_output=True).stdout.strip().removeprefix('shlibs:Depends=')
        if not dependencies or '\n' in dependencies:
            raise RuntimeError('unexpected dpkg-shlibdeps output')
        # QML imports and dynamically loaded SVG support are invisible to ELF dependency scanning.
        dependencies += ', qml6-module-qtquick, qml6-module-qtqml, qml6-module-qtqml-workerscript, qml6-module-qtquick-window, libqt6svg6, qt6-wayland'
        arch = run('dpkg', '--print-architecture', capture_output=True).stdout.strip()
        size = sum(p.stat().st_size for p in stage.rglob('*') if p.is_file()) // 1024 + 1
        (stage / 'DEBIAN').mkdir()
        (stage / 'DEBIAN/control').write_text(
            'Package: quickshell\nVersion: 0.3.1-0uconsole1\n'
            f'Architecture: {arch}\nInstalled-Size: {size}\n'
            'Maintainer: uConsole local builder <root@localhost>\n'
            'Section: x11\nPriority: optional\n'
            f'Depends: {dependencies}\n'
            'Homepage: https://quickshell.org/\n'
            'Description: Flexible QtQuick desktop shell toolkit\n'
            ' Local Trixie build with default features. Requires a shell configuration.\n')
        package = output / f'quickshell_0.3.1-0uconsole1_{arch}.deb'
        run('dpkg-deb', '--root-owner-group', '--build', str(stage), str(package))
        print(package)


if __name__ == '__main__':
    main()
