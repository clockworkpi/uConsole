"""Release fail-closed orchestration; never contact GitHub or tag this checkout."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflowTests(unittest.TestCase):
    def test_publication_requires_native_packages_and_full_host_tests(self):
        release = (ROOT/'.github/workflows/release.yml').read_text()
        host = (ROOT/'.github/workflows/emulator.yml').read_text()
        self.assertIn('  workflow_call:\n', host)
        self.assertIn('  host-tests:\n    name: Host and GUI tests\n'
                      '    uses: ./.github/workflows/emulator.yml\n', release)
        publish = release.split('\n  publish:\n', 1)[1]
        self.assertIn('    needs: [packages, host-tests]\n', publish)
        self.assertIn("    if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')\n", publish)
        self.assertIn('xvfb-run -a make check', host)
        self.assertIn('make check PYTHON="$(brew --prefix)/bin/python3.12"', host)
        self.assertIn('python3 "$resources/tools/build_emulator_qemu.py" --jobs 4 > "$evidence/qemu-rebuild.log" 2>&1', release)


@unittest.skipUnless(os.name == 'posix', 'Release shell runs on Linux or macOS')
class ReleaseShellTests(unittest.TestCase):
    def run_release(self, host='Linux', fail='', gui=True, dry_run=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root/'calls.log'
            (root/'scripts').mkdir()
            helper = root/'scripts/platform.sh'
            helper.write_text('#!/bin/sh\n[ "$1" = python ] || exit 97\nprintf "%s\\n" "$FORGE_TEST_PYTHON"\n')
            helper.chmod(0o755)
            scripts = {
                'git': '''#!/bin/sh
printf 'git:%s\n' "$*" >> "$FORGE_TEST_LOG"
case "$*" in
 'status --porcelain'|'fetch origin master --tags') exit 0 ;;
 'branch --show-current') printf 'master\n' ;;
 'rev-parse HEAD'|'rev-parse origin/master') printf 'fixture-revision\n' ;;
 'tag --list v[0-9]*.[0-9]*.[0-9]*') printf 'v1.0.2\n' ;;
 'rev-parse v1.1.0') exit 1 ;;
 *) printf 'Unexpected git command: %s\n' "$*" >&2; exit 99 ;;
esac
''',
                'gh': '#!/bin/sh\n[ "$*" = "auth status" ]\n',
                'uname': '#!/bin/sh\nprintf "%s\\n" "$FORGE_TEST_OS"\n',
                'make': '''#!/bin/sh
printf 'make:%s\n' "$*" >> "$FORGE_TEST_LOG"
[ "$1" != "$FORGE_TEST_FAIL" ] || exit 17
''',
            }
            if gui:
                scripts['xvfb-run'] = '''#!/bin/sh
printf 'xvfb:%s\n' "$*" >> "$FORGE_TEST_LOG"
[ "$1" = '-a' ] || exit 98
shift
exec "$@"
'''
            for name, source in scripts.items():
                path = root/name
                path.write_text(source)
                path.chmod(0o755)
            (root/'python3').symlink_to(sys.executable)
            command = ['/bin/bash', str(ROOT/'scripts/release.sh')]
            if dry_run: command.append('--dry-run')
            command.append('minor')
            result = subprocess.run(command, cwd=root, text=True, capture_output=True,
                env=dict(os.environ, PATH=str(root), FORGE_TEST_LOG=str(log),
                         FORGE_TEST_OS=host, FORGE_TEST_FAIL=fail,
                         FORGE_TEST_PYTHON=str(root/'python3')), timeout=30)
            return result, log.read_text().replace(str(root/'python3'), '<python>').splitlines()

    def test_linux_qualifies_gui_and_devices_before_package(self):
        result, calls = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in calls if line.startswith(('make:', 'xvfb:'))],
                         ['xvfb:-a make check PYTHON=<python>', 'make:check PYTHON=<python>', 'make:emulator-build',
                          'make:check-emulator', 'make:package'])
        self.assertIn('dry run did not create v1.1.0', result.stdout)
        self.assertFalse(any(line.startswith('git:tag -a') for line in calls))

    def test_mac_uses_native_gui(self):
        result, calls = self.run_release(host='Darwin', gui=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in calls if line.startswith('make:')],
                         ['make:check PYTHON=<python>', 'make:emulator-build', 'make:check-emulator', 'make:package'])
        self.assertFalse(any(line.startswith('xvfb:') for line in calls))

    def test_linux_missing_virtual_display_fails_before_tests_or_tag(self):
        result, calls = self.run_release(gui=False, dry_run=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('xvfb-run is required', result.stderr)
        self.assertFalse(any(line.startswith(('make:', 'git:tag -a')) for line in calls))

    def test_each_failed_gate_prevents_tagging(self):
        for gate in ('check', 'emulator-build', 'check-emulator', 'package'):
            with self.subTest(gate=gate):
                result, calls = self.run_release(fail=gate, dry_run=False)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertFalse(any(line.startswith(('git:tag -a', 'git:push')) for line in calls))
                self.assertEqual([line for line in calls if line.startswith('make:')][-1].split()[0], 'make:'+gate)

    def test_platform_python_action_uses_launcher_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = root/'python-fixture'
            log = root/'probe.log'
            probe.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FORGE_TEST_LOG"\nexit 0\n')
            probe.chmod(0o755)
            result = subprocess.run(['/bin/bash', str(ROOT/'scripts/platform.sh'), 'python'],
                env=dict(os.environ, PYTHON=str(probe), FORGE_TEST_LOG=str(log)),
                text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(probe))
            self.assertIn('sys.version_info >= (3, 12)', log.read_text())
            self.assertIn('import tkinter', log.read_text())
