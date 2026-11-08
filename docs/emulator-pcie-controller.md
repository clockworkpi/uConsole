# BCM2711 PCIe simulator profiles

The `raspi4b` controller at CPU `0xfd500000` (GPU `0x7d500000`) defaults to a
disconnected port. An optional connected profile uses QEMU's real PCI bridge,
xHCI controller and USB engines. Neither profile qualifies physical PCIe or
Pi 5/RP1. The default FDT profile disables its PCIe node; the connected opt-in retains it.

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
creates a BCM-layout subclass of QEMU `pcie-root-port` at BDF `00:00.0` and `qemu-xhci` at `01:00.0`.
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

## Native FreeBSD FDT and USB loading

The connected opt-in now preserves the `brcm,bcm2711-pcie` FDT node and can coexist
with the separate real GENET profile. The default disconnected profile still
removes PCIe; no firmware FDT properties such as `brcm,enable-ssc` are deleted to
bypass controller initialization.

FreeBSD's existing `bcm2838_pci.c` reads link control/status at the fixed Broadcom
config offset `0xbc`. The generic QEMU root-port PCIe capability was at `0x54`,
so the first node-retained cold boot failed native attach with "controller started
but link is not up". The connected root now places the actual QEMU PCIe capability
at `0xac`, making the real link control/status DWORD available at `0xbc`.
The layout pattern follows official QEMU commits
[`7ae9ecd37fdb87bd193bdc1fdcf81f60e2dc7864`](https://github.com/qemu/qemu/commit/7ae9ecd37fdb87bd193bdc1fdcf81f60e2dc7864)
and `7ff1107bf2cd4118a789ecdc66f70c22120d980b`, with upstream attribution retained.
It preserves QEMU IDs and real bridge behavior; it does not replace a status read
with fixed success bits or import upstream's broad controller register shadow.

The retained Ubuntu U-Boot lacks the BCM PCI host driver. A separately built,
unmodified upstream U-Boot v2025.10 `rpi_4_defconfig`, pinned to commit
`e50b1e8715011def8aff1588081a2649a2c6cd47`, executes that driver. Its first cold run
faulted at CPU `0xfd501100`, ESR `0x96000250`, while starting the real SSC MDIO
sequence. The selected connected PHY profile therefore implements controller
MDIO ADDR/WRITE/READ at `0x1100/0x1104/0x1108`, port0 bank select `0x1f`, SSC bank
`0x1100`, control register2 and read-only status register1. WRITE DONE clears on
completion and READ DONE sets with data. SSC enable/override bits control SSC
status; the bridge/SerDes state controls PLL availability. Unknown PHY tuples read
as absent `0xffff`; writes do not fabricate another PHY. This is a synchronous
selected SSC profile, without electrical or analog timing fidelity. The primary
contract is the pinned U-Boot `drivers/pci/pcie_brcmstb.c:210-286` and included
`arch/arm/mach-bcm283x/include/mach/acpi/bcm2711.h`; copyright/license provenance is
retained in the implementation. The firmware keeps `brcm,enable-ssc` enabled.

Native FreeBSD programs an identity 8GiB BAR2 aperture but restricts child DMA below
960MiB. Its FDT outbound range maps PCI `0xc0000000` to CPU `0x600000000`.
The native-init test reproduces that setup and cold bridge-window relocation,
then executes real xHCI DMA/INTx, reset and migration. Native MSI initialization
registers are exercised, but no endpoint MSI/EOI support is claimed. The existing
xHCI endpoint has MSI/MSI-X disabled and uses native DT INTx without guest tunables.

The new root-port capability layout has a distinct VMState section identity;
older connected-layout streams are rejected rather than silently misinterpreted.
The disconnected controller's migration contract is unchanged and was tested
against the older qualified binary. A connected-only optional MDIO subsection
preserves selector, SSC and transaction state; reset clears that state.

The frozen model candidate is generation
`0242009a2e892f7efaaacf6a014b41cab35888f84647256504eccd2493bef1e6`, system binary
SHA256 `08d5088766b2670847f0ec1c57cc7aeceb24f6c3fc98caff6ed2f6265718d834`.
The pinned U-Boot binary SHA256 is
`0679280e659e284cfebb3fa568747c3fcab5bdcc4784f434e8f89003247757b4`.
Private retained evidence under
`/home/jkh/daemonarchy-debug/pi4-fdt-usb-20261108/` includes `gate1` (native attach
failure), `uboot4` (MDIO abort), `native-mdio.log` (controller/data-plane controls),
and `usb5` (USB-only EFIloader, native USB/UFS root and init, no SD drive).
The 65-second `usb5` observer ended by host cleanup while the guest remained
running; it does not establish completed diagnostics, network transfer or clean
shutdown. The subsequent public USB-only controller passed 34/34 checks on 2GiB and 35/35
on 8GiB with the same frozen QEMU/U-Boot and the newest owned GPIO-hog kernel.
It rejected SD media, verified USB EFIloader paths and exact virtual disk size,
and authenticated the mounted UFS root's GEOM provider as `da0`. Native
`bcm2838_pcib`/PCI/xHCI/umass/da0 and GENET completed trusted bidirectional 1MiB
SCP plus roundtrip hashes, seven probes, 18 durable files and natural guest reset.
The 8GiB guest reported `hw.physmem=8430018560`. Both base images stayed unchanged.
Retained receipts are
`/home/jkh/daemonarchy-debug/pi4-usb-network-audit-20261108/freebsd-usb-hogs-{2g,8g}/result.json`.
A separate 8GiB pftf/ACPI USB compatibility run passed all 20 checks on the same
model, including full-memory bounds and natural reset; its receipt is
`pi4-fdt-usb-20261108/uefi-usb-hogs-8g/result.json` under the debug directory.
These are native guest runs in the simulator. QEMU still injects U-Boot; they do
not qualify EEPROM/VideoCore boot or physical hardware.

## Reset, interrupts and migration

Bridge reset and PERST have independent readback. Bridge reset clears programmed
control/window state and interrupt banks; window programming is ignored while it is held.
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
python3 tools/test_emulator_pcie_fdt.py --qemu build/emulator/qemu-build/qemu-system-aarch64
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
