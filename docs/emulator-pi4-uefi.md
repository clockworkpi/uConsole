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
| With disconnected PCIe controller profile | Passes the PCIe reset/init sequence, then aborts reading GENET RBUF flush at `0xfd580008`; ESR_EL2 `0x96000010` |
| With disconnected GENET controller profile | Reaches the firmware setup/boot prompt without either controller abort |
| With secure GIC, EMMC2 card and manual SDHCI stop repair | Completes GPT/FAT reads and writes; a later Arasan MMC write encounters a translation fault on buffer `0x100003b6000` |

These EDK2 runs have not reached the FreeBSD loader. The PCIe address is
`PCIE_REG_BASE + PCIE_RGR1_SW_INIT_1` in the
[EDK2 BCM2711 definitions](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Include/IndustryStandard/Bcm2711.h).
The [host bridge constructor](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Library/Bcm2711PciHostBridgeLib/Bcm2711PciHostBridgeLibConstructor.c)
reads it while resetting the controller. The new
[PCIe](emulator-pcie-controller.md) and [GENET](emulator-genet-controller.md)
profiles implement control/reset/interrupt state with disconnected links;
they do not implement endpoint or network data planes. These were measured
simulator gaps, not demonstrated FreeBSD defects.

## Secure firmware interrupt profile

TF-A v2.9 initializes interrupts as nonsecure Group1 before entering UEFI.
QEMU's default Pi 4 GIC has security extensions disabled. A register snapshot
at the stalled boot prompt showed the timer expired and unmasked, PPI 30
pending/enabled, all PPIs in Group1, but only Group0 enabled at both GIC
interfaces; HPPIR reported 1022. The default model interprets UEFI's nonsecure
control writes without the security-bank aliases TF-A expects.

For this firmware experiment select:

```sh
-global arm_gic.has-security-extensions=on
```

The unchanged firmware then advances its boot countdown; its timer is serviced
instead of remaining expired. This profile follows the
[TF-A RPi4 setup](https://github.com/ARM-software/arm-trusted-firmware/blob/v2.9/plat/rpi/rpi4/rpi4_bl31_setup.c),
[TF-A Group1 initialization](https://github.com/ARM-software/arm-trusted-firmware/blob/v2.9/drivers/arm/gic/v2/gicv2_helpers.c)
and [QEMU register aliases](https://github.com/qemu/qemu/blob/v10.2.4/hw/intc/arm_gic.c).
Direct Linux/FDT boot keeps its existing GIC defaults. Further EDK2 console/SD
handoff testing remains open; countdown progress is not an OS boot result.

## Firmware load contract

Use a fresh writable qcow2 overlay, leaving the raw diagnostic image intact.
The SD model requires a power-of-two virtual capacity. For example, with
`qemu-img` from this repository's patched build:

```sh
qemu-img create -f qcow2 -F raw -b /absolute/path/pi4-discovery-ready.img pi4-uefi.qcow2 512M
qemu-system-aarch64 -machine raspi4b -accel tcg \
  -global arm_gic.has-security-extensions=on \
  -drive if=none,id=sdcard,format=qcow2,file=pi4-uefi.qcow2 \
  -device sd-card,bus=/generic-sdhci/sd-bus,drive=sdcard \
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
The explicit SD bus selects the pinned model's EMMC2 controller; its default
`if=sd` attachment selects the older controller through the GPIO mux.
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

The controller-only checkpoint binary SHA-256 is
`cf65cb291e517a6a3db41bd3db5990006da003f7eb5d90fb66ae901be1f30eec`.
RNG200, PCIe and GENET register/reset/IRQ and migration tests pass; GENET also
has actual CPU controls proving reserved MMIO aborts and mapped MMIO succeeds.
The Linux ARM64 native package built and passed extracted-archive/GUI
validation, and all three suites passed against its extracted emulator binary.

A separate Daemonarchy U-Boot/FDT profile boots FreeBSD with four active CPUs,
native SD root, init, seven completed diagnostic probes and synchronized reset.
Offline readback preserves ten files; all 15 checks pass on this exact binary.
The new BCM2712-compatible kernel also passes that Pi 4 control. Those runs
keep the unsupported controller FDT nodes disabled and do not establish EDK2,
Pi 5 or controller data-plane acceptance. See
[the Daemonarchy record](https://github.com/jordanhubbard/daemonarchy/blob/work/pi-simulator-checkpoint/docs/testing-pi4-uboot-fdt-20261108.md).

See [emulator scope](emulator.md) for the other missing devices. EDK2's next
step is a complete SD/console/loader handoff; silently skipping failed MMIO
would hide unsupported hardware.

## SD transfer lifecycle repair

The [SDHCI repair](emulator-sdhci-stop.md) fixes a separately reproduced
manual-CMD12 host-busy defect, cancels aborted/reset ADMA callbacks and guards
SDMA boundary continuation against stale transfers. Real PIO and four
independent DMA baseline failures now pass. The exact binary is SHA-256
`7fe73e7a5b2cf80a05ac373687b92682a728e0b2ce692045e76161c013d366d5`.
It passes the full FreeBSD control (15 checks) and an interactive Linux
`6.12.62-v8+` control (10 checks, four CPUs, 64-bit userspace, actual SD root,
natural poweroff). Both base images remain unchanged. Its native package
passes extracted-archive/GUI validation and all five model/SD regression
suites against the packaged emulator.

EDK2 with this repair progresses through SD reads/writes but still does not
reach the FreeBSD loader. A breakpoint at the first synchronous exception
captures ESR_EL2 `0x96000004`, ELR_EL2 `0x391e83c8` in Arasan MMC write and
FAR_EL2 `0x100003b6000`. Investigation is tracing the incoming buffer and its
preservation across callbacks/interrupts. Mapping that invalid address or
discarding the exception would not establish a valid firmware handoff.
