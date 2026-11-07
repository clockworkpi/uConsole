# Pi 4 UEFI simulator checkpoint, 2026-11-07

This is a BCM2711/Pi 4 experiment using the existing partial `raspi4b`
model. It does not emulate BCM2712, RP1 or Pi 5. Physical boards remain
separate acceptance targets. No physical board needs to be restarted for this
experiment.

## Control and measured blockers

An isolated qcow2 overlay of the existing CM4 image booted Linux
`6.12.62-v8+`, mounted its actual SD root filesystem and ran an interactive
probe: aarch64 kernel, 64-bit userspace, four online CPUs and 1,902,264 KiB
MemTotal. The guest then powered off cleanly. This establishes the Linux
simulation control, not a FreeBSD or firmware boot.

The Daemonarchy Pi 4 diagnostic image (389,056,000 bytes, SHA-256
`d6e28c32a439e8708978f3527835377bbd64d648022d83946d4abdaeba0af2a0`)
was attached through a new 512 MiB qcow2 overlay. Its pftf UEFI v1.53
`RPI_EFI.fd` has SHA-256
`8334d9d3c3fe5341fb9876f2ddf653eca7b260ee2cda669c5446a0dd7f0104c5`.
The firmware and base image were unchanged between these tests:

| Model | Observed stop |
| --- | --- |
| Existing patched QEMU | UEFI synchronous external abort on a write to RNG200 threshold `0xfe104010`; ESR_EL2 `0x96000050` |
| With RNG200 model | UEFI proceeds past RNG initialization, then external abort on a read of PCIe reset register `0xfd509210`; ESR_EL2 `0x96000010`, ELR_EL2 `0x3969f4d4` |

Neither run reaches the FreeBSD loader. The second address is
`PCIE_REG_BASE + PCIE_RGR1_SW_INIT_1` in the
[EDK2 BCM2711 definitions](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Include/IndustryStandard/Bcm2711.h).
The [host bridge constructor](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Library/Bcm2711PciHostBridgeLib/Bcm2711PciHostBridgeLibConstructor.c)
reads it while resetting the controller. PCIe is absent from this QEMU model.
These are measured simulator gaps, not demonstrated FreeBSD defects.

## Firmware load contract

Use a fresh writable qcow2 overlay, leaving the raw diagnostic image intact.
The SD model requires a power-of-two virtual capacity. For example, with
`qemu-img` from this repository's patched build:

```sh
qemu-img create -f qcow2 -F raw -b /absolute/path/pi4-discovery-ready.img pi4-uefi.qcow2 512M
qemu-system-aarch64 -machine raspi4b -accel tcg \
  -drive if=sd,format=qcow2,file=pi4-uefi.qcow2 \
  -device loader,file=RPI_EFI.fd,addr=0,cpu-num=0,force-raw=on \
  -device loader,addr=0x1000,cpu-num=1 \
  -device loader,addr=0x1000,cpu-num=2 \
  -device loader,addr=0x1000,cpu-num=3 \
  -display none -serial file:uart0.log -serial file:uart1.log \
  -monitor none -nic none \
  -qmp unix:qmp.sock,server=on,wait=off \
  -d guest_errors,unimp -D guest-errors.log
```

Run this in a new private directory with absolute paths to the patched QEMU
binaries. `RPI_EFI.fd` is an armstub image linked at zero; QEMU's `-bios`
loads it at the wrong address. Secondary CPUs enter its stub at `0x1000`.
This bypasses the VideoCore/EEPROM boot chain; it does not test that chain.
Both UART logs matter. Preserve logs and a GDB exception-register snapshot
before stopping a failed run through QMP. Do not infer firmware success from
an empty UART log or a live QEMU process.

## RNG200 scope and validation

The new model maps the BCM2711 RNG200 register block at `0xfe104000` and
routes its IRQ through GIC SPI 125. It uses QEMU's host random service and
produces words on demand. It supplies enable/disable, both software resets,
word/threshold accounting, startup status, write-one-to-clear status,
interrupt masks and migration state. It does not simulate oscillator timing,
entropy health tests or certify entropy quality. The normal Linux fixture
still disables its FDT node, so the Linux control does not qualify the guest
RNG driver.

```sh
python3 tools/test_emulator_rng200.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

On Linux ARM64, QEMU 10.2.4 built successfully with the system GCC, glib and
pkg-config environment. The register/reset/IRQ controls and real QEMU
migration with reset asserted and deasserted passed. The 33 emulator host
tests and 11 source-cache/extraction tests also passed. These checks do not
close the PCIe blocker or native board acceptance.

See [emulator scope](emulator.md) for the other missing devices. The next UEFI
step requires an explicit PCIe model or a separately identified firmware
profile; silently skipping failed MMIO would hide the unsupported hardware.
