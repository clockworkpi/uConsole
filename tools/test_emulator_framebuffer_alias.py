#!/usr/bin/env python3
"""Prove mailbox GPU alias -> EDK2 CPU address -> visible QMP framebuffer pixels."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from test_emulator_pmic import connect
from uconsole_emulator import executable, qmp

MAILBOX = 0xfe00b880
GPU_ALIAS = 0xc0000000
WIDTH, HEIGHT = 32, 16


def ppm(path):
    magic, dimensions, maximum, pixels = path.read_bytes().split(b"\n", 3)
    assert magic == b"P6" and maximum == b"255", "unexpected QEMU screendump format"
    assert dimensions == f"{WIDTH} {HEIGHT}".encode(), dimensions
    assert len(pixels) == WIDTH * HEIGHT * 3
    return pixels


def run(args, output):
    with tempfile.TemporaryDirectory(prefix="uc-fb-alias-") as sockets:
        sockets = Path(sockets)
        with (output / "qemu.log").open("wb") as log:
            process = subprocess.Popen([
                args.qemu, "-M", "raspi4b", "-accel", "qtest", "-S",
                "-display", "none", "-serial", "none", "-monitor", "none",
                "-qtest", f"unix:{sockets}/test,server=on,wait=off",
                "-qmp", f"unix:{sockets}/qmp,server=on,wait=off",
            ], stdout=log, stderr=log)
            try:
                with connect(sockets / "test", process) as sock:
                    stream = sock.makefile("rwb", buffering=0)

                    def command(text):
                        stream.write((text + "\n").encode())
                        line = stream.readline().decode().strip()
                        assert line.startswith("OK"), line
                        return line

                    def write(address, value):
                        command(f"writel {address:#x} {value:#x}")

                    def read(address):
                        return int(command(f"readl {address:#x}").split()[1], 16)

                    def request(buffer, bus_alias):
                        tags = [(0x48003, [WIDTH, HEIGHT]), (0x48004, [WIDTH, HEIGHT]),
                                (0x48005, [32]), (0x48006, [1]),
                                (0x40001, [4096, 0]), (0x40008, [0])]
                        words = [0, 0]
                        offsets = {}
                        for tag, values in tags:
                            offsets[tag] = len(words)
                            words.extend([tag, len(values) * 4, 0, *values])
                        words.append(0)
                        words[0] = len(words) * 4
                        for index, word in enumerate(words):
                            write(buffer + index * 4, word)
                        write(MAILBOX + 0x20, buffer | bus_alias | 8)
                        assert read(MAILBOX) == buffer | bus_alias | 8
                        assert read(buffer + 4) == 0x80000000
                        for tag, values in tags:
                            offset = offsets[tag]
                            assert read(buffer + (offset + 2) * 4) == 0x80000000 | (len(values) * 4)
                        allocation = buffer + (offsets[0x40001] + 3) * 4
                        assert read(allocation + 4) == WIDTH * HEIGHT * 4
                        pitch = read(buffer + (offsets[0x40008] + 3) * 4)
                        assert pitch == WIDTH * 4
                        return read(allocation)

                    framebuffer = qmp(sockets / "qmp", "qom-get", {
                        "path": "/machine/soc/peripherals/fb", "property": "vcram-base"}) + 0x100000
                    observations = []
                    for cycle, bus_alias in enumerate((0, GPU_ALIAS, 0)):
                        if cycle == 2:
                            qmp(sockets / "qmp", "system_reset")
                        address = request(0x10000 + cycle * 0x1000, bus_alias)
                        edk2_cpu = (address - GPU_ALIAS) & 0xffffffff
                        linux_cpu = address & 0x3fffffff
                        assert linux_cpu == framebuffer, (hex(address), hex(framebuffer))
                        if args.expect_original_bug:
                            assert address == framebuffer and edk2_cpu != framebuffer
                        else:
                            assert address == framebuffer | GPU_ALIAS, (
                                f"allocation returned {address:#x}; expected GPU bus alias {framebuffer | GPU_ALIAS:#x}")
                            assert edk2_cpu == linux_cpu == framebuffer
                        # Clear the actual displayed buffer through the CPU bus.
                        command(f"memset {framebuffer:#x} {WIDTH * HEIGHT * 4:#x} 0")
                        before = output / f"{cycle}-before.ppm"
                        qmp(sockets / "qmp", "screendump", {"filename": str(before)})
                        assert not any(ppm(before))
                        words = [0x000000ff, 0x0000ff00, 0x00ff0000]
                        for pixel, word in enumerate(words):
                            write(edk2_cpu + pixel * 4, word)
                            assert read(edk2_cpu + pixel * 4) == word
                        after = output / f"{cycle}-edk2.ppm"
                        qmp(sockets / "qmp", "screendump", {"filename": str(after)})
                        expected = bytes.fromhex("ff000000ff000000ff")
                        if args.expect_original_bug:
                            assert ppm(after) == ppm(before), "negative control unexpectedly reached displayed RAM"
                            # Independent Linux-style decode proves this is the
                            # address contract, rather than a broken display.
                            for pixel, word in enumerate(words):
                                write(linux_cpu + pixel * 4, word)
                            linux = output / f"{cycle}-linux-control.ppm"
                            qmp(sockets / "qmp", "screendump", {"filename": str(linux)})
                            assert ppm(linux)[:9] == expected
                        else:
                            assert ppm(after)[:9] == expected, ppm(after)[:9].hex()
                            assert not any(ppm(after)[9:]), "write changed pixels outside its three-pixel extent"
                        observations.append(dict(request_bus_alias=hex(bus_alias), returned_bus=hex(address),
                                                 edk2_cpu=hex(edk2_cpu), linux_cpu=hex(linux_cpu)))
                    with Path(args.qemu).open("rb") as binary:
                        binary_hash = hashlib.file_digest(binary, "sha256").hexdigest()
                    receipt = dict(status="original-bug-reproduced" if args.expect_original_bug else "passed",
                                   coverage="real QEMU mailbox, CPU writes and QMP RGB pixels; no guest OS boot",
                                   qemu_sha256=binary_hash,
                                   observations=observations, reset_rechecked=True)
                    (output / "result.json").write_text(json.dumps(receipt, indent=2) + "\n")
                    print(json.dumps(receipt))
                qmp(sockets / "qmp", "quit")
                assert process.wait(timeout=10) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", default=executable("qemu-system-aarch64"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expect-original-bug", action="store_true",
                        help="negative control: require the original incorrect alias and invisible EDK2 writes")
    args = parser.parse_args()
    if args.output:
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        run(args, output)
    else:
        with tempfile.TemporaryDirectory(prefix="uc-fb-alias-result-") as directory:
            run(args, Path(directory))


if __name__ == "__main__":
    main()
