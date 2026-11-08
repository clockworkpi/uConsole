# BCM2711 PCIe simulator profiles

The `raspi4b` controller at CPU `0xfd500000` (GPU `0x7d500000`) defaults to a
disconnected port. An optional connected profile uses QEMU's real PCI bridge,
xHCI controller and USB engines. Neither profile qualifies physical PCIe or
Pi 5/RP1. The ordinary Linux FDT fixture still disables its PCIe node.

## Disconnected profile

With no additional options, firmware can reset the controller and discover no
endpoint while booting from SD. Root-complex strap and v3.20 controller revision
are explicit simulator values. PHY-up and data-link-active remain clear, and
all configuration reads return all ones. Stored window values do not activate
a data plane. `tools/test_emulator_pcie.py` checks absent identity, reset/window
semantics, IRQ banks and migration with resets held and released.

## Connected profile

Enable the virtual endpoint with:

```sh
-global bcm2838-pcie-disconnected.connected-xhci=on
```

The retained QOM type name is also the default-profile interface. Opting in
creates a real `pcie-root-port` at BDF `00:00.0` and `qemu-xhci` at `01:00.0`.
They retain their QEMU identities and ordinary PCI configuration/BAR semantics;
this does not impersonate a VL805. The USB bus is `bcm-xhci.0`, so an actual
USB disk can be attached with `-device usb-storage,bus=bcm-xhci.0,drive=stick`
and an independently defined block backend.

Root-port configuration is directly accessible through the controller's low
configuration offsets. Downstream configuration uses INDEX `0x9000` with
`bus << 20 | devfn << 12` and DATA `0x8000 + register`. Missing BDFs remain
absent. Four programmed outbound windows translate CPU addresses into the real
PCI memory space. Firmware's window maps CPU `0x600000000` to PCI `0xf8000000`;
bridge memory forwarding, endpoint memory decode and controller reset/SCB state
all gate access.

BAR2 supplies a programmed inbound DMA alias to actual board RAM. For example,
`0x11` maps PCI zero to ARM zero with a 4 GiB aperture clipped to available RAM;
`0xc000000f` maps PCI `0xc0000000` to ARM zero with a 1 GiB aperture. Disabled
or unsupported mappings do not silently provide DMA. Enabling unsupported
BAR1/BAR3 returns an MMIO error. Native endpoint INTx A–D feeds GIC SPI
143–146; endpoint MSI/MSI-X capabilities are disabled in this profile. Controller
and software MSI banks separately retain their SPI 147/148 controls.

The board enables the optional `compact-mmio` xHCI property. It keeps all actual
register operations and transfer engines but advertises a 4 KiB BAR with runtime
registers at `0x800` and doorbells at `0xc00`. The standard operational, port,
16-interrupter and 64-slot register ranges fit that aperture. This matches the
fixed 4 KiB resource in the [pinned firmware Xhci.asl](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Platform/RaspberryPi/AcpiTables/Xhci.asl).
Generic QEMU xHCI retains its default 16 KiB layout; compact mode explicitly
requires `msix=off` because its MSI-X table/PBA use the larger layout.

This profile has no electrical PHY/MDIO/SerDes timing, VL805-specific behavior
or endpoint MSI implementation. Actual USB storage bootstrap is qualified
separately from controller tests; a command-ring test alone does not establish
UEFI or kernel boot.

## Reset, interrupts and migration

Bridge reset and PERST have independent readback. Bridge reset clears programmed
control/window state and interrupt banks; writes are ignored while it is held.
In the connected profile, assertion also resets the actual downstream devices
and gates forwarding/DMA. Power-on and system reset hold both resets asserted.
Releasing reset provides the selected profile's link status.

Controller interrupt banks support software set, W1C, mask-set and mask-clear.
Unsupported controller/PHY offsets return transaction errors rather than
blanket zero-filled RAM. Same-profile migration preserves controller state,
rebuilds translations, restores IRQ levels and migrates actual PCI/xHCI engine
state. A property mismatch is rejected. A focused generic xHCI patch prevents
post-load MSI-X vector accounting when MSI-X is absent; without it, this INTx
profile aborted during incoming migration.

The controller layout follows [Linux v6.12](https://github.com/torvalds/linux/blob/v6.12/drivers/pci/controller/pcie-brcmstb.c),
the [BCM2711 interrupt binding](https://github.com/torvalds/linux/blob/v6.12/arch/arm/boot/dts/broadcom/bcm2711.dtsi),
and the [pinned EDK2 constructor](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Library/Bcm2711PciHostBridgeLib/Bcm2711PciHostBridgeLibConstructor.c).
These sources do not establish hardware-equivalent timing or reset defaults.

## Validation

```sh
python3 tools/test_emulator_pcie.py --qemu build/emulator/qemu-build/qemu-system-aarch64
python3 tools/test_emulator_pcie_xhci.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The connected test enumerates actual bridge/endpoint configuration, sizes the
BAR, executes xHCI NoOp commands and reads completion events DMA-written into
ARM RAM through identity and nonidentity inbound mappings. It checks native GIC
INTx/W1C, bridge memory and bus-master gating, disabled inbound mappings,
absent BDFs, pending-event migration followed by another command, and PERST and
system-reset recovery. Runtime proofs and the full USB firmware/kernel path are
recorded separately in the [Pi 4 UEFI checkpoint](emulator-pi4-uefi.md).

On 2026-11-08, the compact connected test passed on pinned QEMU 10.2.4 binary
SHA-256 `b6c676c083bbe539514e635b3149d0ceacffecb866d4db0dc154ff24eff430a5`.
It additionally checked that the last advertised interrupter/slot registers fit
the 4 KiB aperture, generic `virt` xHCI retains its 16 KiB default, explicit
compact mode executes a real NoOp, and compact plus MSI-X is rejected. The
disconnected PCIe, framebuffer, RNG200, GENET, SD PIO/ACMD22 and four DMA controls
passed on the same binary, as did all 11 source-cache tests. A complete Linux
control reached an interactive four-CPU, 64-bit ext4 root and powered down
naturally, preserving its original base image. These controls do not establish
completion of the separate ACPI USB bootstrap experiment.

## Chained USB storage completion repair

The first ACPI USB run with thermal status available mounted the UFS root, then
waited on a large read of the kernel's CTF data. An observer run eventually
continued after five READ(10) retries and an I/O error; that run is disqualified
as clean storage acceptance. Typed kernel inspection found a 128 KiB bulk-IN
transfer whose first 64 KiB completion had arrived while the second was missing.
The timer advanced normally and no interrupt remained pending.

An independent `virt`/`qemu-xhci`/`usb-storage` fixture reproduces the defect
without the Broadcom controller. It checks the complete 128 KiB payload against
a patterned disk: all bytes arrive, but QEMU reports only the first IOC-bearing
TRB in the chain. A transfer-wide `reported` latch suppresses the final event.
The focused patch permits another ordinary successful IOC event while retaining
the previous terminal-short suppression and error return. This follows the
per-TRB completion contract in [Intel xHCI sections 4.10.1 and 4.11.7](https://www.intel.ie/content/dam/www/public/us/en/documents/technical-specifications/extensible-host-controler-interface-usb-xhci.pdf).
It does not claim to repair every pre-existing compound short-transfer or
later-error attribution case.

```sh
python3 tools/test_emulator_xhci_storage.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The storage test covers actual 64/128 KiB reads, a single final IOC, two chained
IOCs, terminal-short behavior, successful Event Data pointers and byte counts,
an actual USB transport STALL, and migration with pending completion interrupts
followed by another disk read. `--expect-missing-ioc` requires the unfixed
baseline failure instead of accepting it as normal behavior. The separate clean
ACPI USB boot must also reject CAM retries and filesystem I/O errors; reaching
init after a failed optional kernel read is insufficient.

The storage fix passed these controls on QEMU binary SHA-256
`c392d7c5b3a56f7afc01887a679e878c40953b0da0fde9dc0fd2edda4d31740d`.
All eight prior model suites and 11 source-cache tests also passed. The same
storage fixture independently requires the missing final event on the immutable
pre-fix `bb335a6d…` baseline while verifying its complete disk payload.

A fresh uninterrupted ACPI USB experiment on this exact `c392d7c5…` binary
then passed all 19 acceptance checks: EFI loader, native ACPI xHCI/USB storage,
UFS root, four active CPUs, seven completed probes and 10 durable diagnostic
files. Its kernel log passed the storage I/O cleanliness guard, its source image
remained unchanged, and the only stop sequence was a guest-requested reset.
The earlier debugger-observed run remains disqualified. This is simulator
bootstrap evidence, not physical Pi 4 or Pi 5 acceptance.
