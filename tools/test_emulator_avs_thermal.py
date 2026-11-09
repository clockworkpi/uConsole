#!/usr/bin/env python3
"""BCM2711 AVS sensor MMIO, GPU DMA alias, bounded injection, reset and migration."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import tempfile

from test_emulator_adc_migration import Machine, migrate
from uconsole_emulator import executable, qmp

BASE = 0xfd5d2200
GPU_BASE = 0x7d5d2200
DMA = 0xfe007000
DEVICE = "/machine/soc/peripherals/avs-thermal"
PROPERTY = "temperature-millicelsius"


def set_temperature(machine, value):
    qmp(machine.monitor, "qom-set", {"path": DEVICE, "property": PROPERTY, "value": value})


def temperature(machine):
    return qmp(machine.monitor, "qom-get", {"path": DEVICE, "property": PROPERTY})


def status(machine, requested):
    value = machine.read(0)
    assert value & (1 << 10), "Linux thermal status is not valid"
    assert value & ~0x7ff == 0, hex(value)
    raw = value & 0x3ff
    decoded = 410040 - 487 * raw
    assert abs(decoded - requested) <= 244, (requested, decoded)
    return value, decoded


def gpu_read(machine):
    # Use the actual BCM2835 DMA engine's GPU address space, not the CPU
    # address 0x7d5d2200 (which is unrelated high CPU RAM in this model).
    cb, destination = 0x30000, 0x30100
    words = (0, GPU_BASE, destination, 4, 0, 0, 0, 0)
    for index, word in enumerate(words):
        machine.command(f"writel {cb + index * 4:#x} {word:#x}")
    machine.command(f"writel {destination:#x} 0xdeadbeef")
    machine.command(f"writel {DMA:#x} 0x80000000")
    machine.command(f"writel {DMA + 4:#x} {cb:#x}")
    machine.command(f"writel {DMA:#x} 1")
    return int(machine.command(f"readl {destination:#x}").split()[1], 16)


def exercise(binary):
    with tempfile.TemporaryDirectory(prefix="uc-avs-") as tmp, ExitStack() as stack:
        root = Path(tmp)
        machine = Machine(root, "sensor", stack, base=BASE, binary=binary)
        assert temperature(machine) == 42000
        value, decoded = status(machine, 42000)
        assert value == 0x6f4 and decoded == 41868
        readings = []
        for requested in (-40000, -12345, 0, 42000, 85000, 125000):
            set_temperature(machine, requested)
            assert temperature(machine) == requested
            value, decoded = status(machine, requested)
            assert gpu_read(machine) == value, "CPU/GPU sensor paths differ"
            readings.append(decoded)
        assert readings == sorted(readings), readings

        # Invalid host injections cannot silently clamp or alter the reading.
        for invalid in (-40001, 125001, -(1 << 63), (1 << 63) - 1, True, "42000"):
            before = temperature(machine), machine.read(0)
            try:
                set_temperature(machine, invalid)
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid temperature accepted: {invalid!r}")
            assert (temperature(machine), machine.read(0)) == before

        # The sensor is read-only. Unsupported widths, alignment and the
        # adjacent hole must not masquerade as a valid temperature register.
        before = machine.read(0)
        for operation in ("writeb", "writew", "writel", "writeq"):
            machine.command(f"{operation} {BASE:#x} 0")
            assert machine.read(0) == before and temperature(machine) == 125000
        for operation, offset in (("readb", 0), ("readw", 0), ("readq", 0),
                                  ("readl", 1), ("readl", 4)):
            reply = int(machine.command(f"{operation} {BASE + offset:#x}").split()[1], 16)
            assert reply == 0, (operation, offset, reply)
            assert machine.read(0) == before
        machine.command(f"writel {BASE + 4:#x} 0xffffffff")
        assert machine.read(0) == before
        qmp(machine.monitor, "system_reset")
        assert temperature(machine) == 42000
        assert status(machine, 42000)[0] == 0x6f4
        assert gpu_read(machine) == 0x6f4
        print("PASS: AVS CPU/GPU DMA alias, quantized calibration, bounded injection, read-only/width/hole controls and reset")


def migration(binary):
    for requested in (-40000, 125000):
        with tempfile.TemporaryDirectory(prefix="uc-avs-mig-") as tmp, ExitStack() as stack:
            root = Path(tmp)
            source = Machine(root, "source", stack, base=BASE, binary=binary)
            target = Machine(root, "target", stack, incoming=True, base=BASE, binary=binary)
            set_temperature(source, requested)
            expected = status(source, requested)[0]
            migrate(source, target, root)
            assert temperature(target) == requested
            assert status(target, requested)[0] == expected
            assert gpu_read(target) == expected
            qmp(target.monitor, "system_reset")
            assert temperature(target) == 42000 and status(target, 42000)[0] == 0x6f4
            print(f"PASS: AVS real QEMU migration, requested millicelsius={requested}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default=executable("qemu-system-aarch64"))
    args = parser.parse_args()
    exercise(args.qemu)
    migration(args.qemu)


if __name__ == "__main__":
    main()
