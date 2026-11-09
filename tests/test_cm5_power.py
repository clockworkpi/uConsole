"""Exercise the power controller without GPIO hardware or network services."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "Code/scripts/uconsole-4g-cm5"


class PowerControlTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.log = self.root / "calls"
        self.env = dict(os.environ, PATH=str(self.root), CALLS=str(self.log))
        self.stub("gpioset", '''#!/bin/sh
if [ "$1" = --version ]; then
    echo "gpioset (libgpiod) v${GPIO_VERSION:-2.1.3}"
    exit 0
fi
echo "gpioset $*" >> "$CALLS"
exit "${GPIO_FAILURE:-0}"
''')
        self.stub("sleep", '#!/bin/sh\necho "sleep $*" >> "$CALLS"\n')

    def stub(self, name, source):
        path = self.root / name
        path.write_text(source)
        path.chmod(0o755)

    def run_script(self, *args):
        return subprocess.run(["/bin/sh", str(SCRIPT), *args], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_enable_without_service_or_network_tools(self):
        result = self.run_script("enable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            "gpioset -t0 -p2s -c gpiochip0 24=1",
            "gpioset -t0 -p2s -c gpiochip0 15=1", "sleep 5",
            "gpioset -t0 -p2s -c gpiochip0 15=0", "sleep 20"])

    def test_disable_without_service_or_network_tools(self):
        result = self.run_script("disable")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [
            "gpioset -t0 -p1s -c gpiochip0 24=0",
            "gpioset -t0 -p5s -c gpiochip0 24=1",
            "gpioset -t0 -p20s -c gpiochip0 24=0", "sleep 5"])

    def test_gpio_failure_stops_sequence(self):
        self.env["GPIO_FAILURE"] = "17"
        for command in ("enable", "disable"):
            with self.subTest(command=command):
                self.log.unlink(missing_ok=True)
                result = self.run_script(command)
                self.assertEqual(result.returncode, 17)
                self.assertEqual(len(self.calls()), 1)
                self.assertNotIn("complete", result.stdout)

    def test_old_libgpiod_rejected_before_gpio_access(self):
        self.env["GPIO_VERSION"] = "1.6.3"
        self.assertNotEqual(self.run_script("enable").returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_dependency(self):
        (self.root / "gpioset").unlink()
        result = self.run_script("enable")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Install gpioset", result.stderr)

    def test_usage_does_not_access_gpio(self):
        for args in ((), ("invalid",), ("enable", "extra")):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)
        (self.root / "gpioset").unlink()
        self.assertEqual(self.run_script("--help").returncode, 0)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
