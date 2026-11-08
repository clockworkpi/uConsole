# Synthetic PCIe2 protocol contract target

This opt-in test profile reuses the qualified BCM2711 SD, PL011, CPU and RAM boot
substrate. It is neither a Pi500 machine nor an RP1 device model. Default raspi4
profiles, QOM names and migration streams retain their existing behavior.

Enable the separate target with:

```sh
-M raspi4b-pcie2-contract \
  -global bcm2838-peripherals.pcie2-contract=on
```

Both selectors are required. The explicit synthetic machine inherits the 8 GiB
BCM2711 substrate and enables QEMU’s existing legal PCI address-zero decoding.
Existing raspi4 machine classes retain their prior PCI address-zero policy.

The target omits old PCIe and GENET device realization and IRQ wiring. Its GIC
has 256 external lines, with timer PPI inputs adjusted for that capacity. MIP
uses SPI 128–191; PCIe2 INTx uses SPI 229–232. The retained Cortex-A72 advertises
44-bit physical addresses, sufficient for the selected MMIO and outbound ranges.

The new host is at CPU `0x1000120000`. A QEMU root port with QEMU IDs and real
configuration/capabilities connects a standard 16 KiB `qemu-xhci` endpoint with
MSI/MSI-X enabled. Its USB bus is `pcie2-xhci.0`. No RP1 or VL805 identity is
fabricated. Four outbound windows and ten inbound register slots have defined
semantics; three active inbound mappings serve peer MMIO, RAM and the raw MIP
route. RAM accesses use the existing physical RAM view, including its holes.
A 64 GiB aperture does not allocate or declare 64 GiB of installed memory.

Provider regions implement SW_INIT SET/CLEAR/STATUS, bridge reset 44, synchronous
virtual calibration, selected AON GPIO/mux/pad fields and MIP edge-host messages.
The 54 MHz MDIO table controls virtual readiness; unknown tuples cannot establish
a link. GPIO RUN, bridge reset, PERST, SerDes and calibration gate the data plane.
This is defined digital protocol behavior, without analog or electrical timing.

The MIP contract uses a fixed message address and integer data indices 0–63.
It supplies genuine endpoint MSI/MSI-X writes through the independently programmed
raw route to GIC edges. Level-mode and VPU delivery are unsupported. Masked edges
drop and post-edge status reads zero in this explicitly synthetic profile. The
maintained reference drivers do not establish invalid-data, CLEAR-payload,
masked-edge replay or reset-default hardware behavior; those policies must never
be presented as measured hardware facts.

Primary register references are raspberrypi/linux commit
`2569533d6c33c15231fbc21704c87fcebc67173c`,
`drivers/pci/controller/pcie-brcmstb.c` and
`drivers/irqchip/irq-bcm2712-mip.c`. The selected cells come from enrolled
`bcm2712-rpi-500.dtb`, SHA256
`400b2da08abb8fd0ffd65085d1e0e1e6c6d01cadd27385403ea3a3fd5a1537f4`.
The pre-implementation window oracle is retained independently as SHA256
`68564ace27325d1bdc7f6d728062d9b3551cf4ca5ef073bc065a9daae9c28c43`.
No Linux driver code is imported into the model; existing QEMU GPL notices remain.

## Synthetic tree and tests

`tools/compose_pcie2_contract_dtb.py` accepts an already UART-fixed base tree:

```sh
python3 tools/compose_pcie2_contract_dtb.py --base cm4-qemu.dtb \
  --selected bcm2712-rpi-500.dtb --output fresh-contract.dtb
```

It preserves selected raw properties and provider physical addresses. Base-only
phandles and binding references move to a separate namespace; the actual base
GIC remains xref 1. Metadata symbols remain path strings, while unsupported
overlay fixups are rejected. The selected provider siblings live on a separate
simple bus so the original SD/UART address translations stay unchanged. The
required direct RP1 simple-bus container is retained disabled, without peripherals.
The output explicitly identifies its synthetic composition and records hashes
and reference-closure checks beside the fresh DTB.

```sh
python3 tools/test_compose_pcie2_contract_dtb.py
python3 tools/test_emulator_pcie2_contract.py --qemu /absolute/new/qemu-system-aarch64 \
  --output /absolute/fresh/qtest-output
```

The first command tests composition and refusal guards. The second is intended
to exercise selected register protocols, real high-physical-buffer xHCI event
DMA, all 64 MSI message indices, MSI-X, GIC delivery, mask behavior, migration
continuation and reset. Neither command establishes native guest CPU interrupt
handling. Native frontend, provider and USB integration requires separate actual
kernel execution with serial/QMP custody and independently checked offline data.

## Current software qualification

The isolated generation
`587ef5e17c583075853b575caa0fa46a3e349f17a7e62a26ae587c97d4e1746e`
now passes those checks. Its system binary SHA256 is
`318f7ff3c7502aad1b5b49e4d682239a74a44351aaefba1b8e8379588966aeee`,
and qemu-img is
`a1bf9caec5efe1c6550c41fffe42e6b8854a3363a4ef6c2011f6ac5feea655d0`.
The actual CPU LDRB returns `0x40`; real USB BOT/SCSI reads verify exact 64 and
128 KiB payloads through high physical buffers and MIP MSI. All 64 message
indices, MSI-X, zero-BAR migration continuation, provider reset and three CPU
MMIO controls pass. An independent CPU probe reproduces the old abort and the
new successful byte load. All 13 default suites and both old-to-new default
migration profiles also pass. The combined receipt binds the earlier negative,
the legacy migration script/log, compiler resources and clean-loader closure:
`/home/jkh/daemonarchy-debug/pi5-pcie2-contract-20261108/qtest-qualification-r7.json`,
SHA256 `fa3fe4029ecbb8ce011d5a75ba384ca36e0b26ecf7606bffe75ca7a475d5bbfd`.

Five complete native FreeBSD default controls also pass on this final binary
with the PCIe2 property off: SD-root and USB-only boot at 2 and 8 GiB, plus
8 GiB UEFI/ACPI USB boot. These preserve native GENET payloads where applicable,
seven successful probes, durable offline evidence and natural guest reset. The
independent receipt is
`/home/jkh/daemonarchy-debug/pi5-pcie2-native-20261108-r2/new-model-defaults/five-default-control-proof.json`,
SHA256 `0f9fe1c08de63c17a76b6932f08551ff89e9e1571ad2c6386bdfb85cdd6517ff`.

Selected PCIe2 full native MSI-X/MSI acceptance remains pending with the
separately built source guard kernel. The default controls and isolated CPU
fixtures do not establish that selected frontend's complete lifecycle or
physical BCM2712/RP1 behavior.

## Retained debugging history

The historical R6 generation
`8034cfba67861cd78db3e59ae7534a8405d1ae3fbc8d0eb6bf6f1802daf96349`
built successfully with an empty environment and explicit system tools. Its
system binary SHA256 is
`a2364bc373e513e4f7d52e431b8f40ead002367d0519e2f912fbb5540ca6115f`;
qemu-img is `d99ddba7de14d1cc7b74559b0853176d9d79521c6a7d245de9c2381cec363e11`.

The selected protocol qtest passed all 64 genuine endpoint MSI message indices,
a separate MSI-X index 63, translated event DMA from physical RAM above 4 GiB,
masking, migration continuation and reset. The retained source GIC acknowledge
sequence is INTIDs 160 through 223, then 223 and 160; the migrated target
acknowledges 160 twice. Three actual AArch64 payloads additionally prove high-MIP
MMIO access and external aborts for unsupported hole and halfword reads. All 13
existing model suites passed on that exact binary, including default PCIe,
GENET, SD, framebuffer and memory controls. The ten composer guards and
independent selected-field readback passed separately.

The receipt is retained at
`/home/jkh/daemonarchy-debug/pi5-pcie2-contract-20261108/qtest-qualification-r6.json`,
SHA256 `b8aa0509b91ebc36141a00f0f695892a9c12d5b61bdf3a8f158fa1b7192dcf77`.
Build receipts bind system compiler/pkg-config resources, actual dependency
headers and clean-loader library hashes. An inherited Homebrew loader path
caused the first link failure; the clean build excludes it. Earlier QOM creation
failures (duplicate child/property name, renamed root-port type and unrealized
old child) remain preserved. The final profile creates the original PCIe child
only in the default branch, leaving its established name and migration format.

A first native cold run reached SD init but its AON GPIO provider failed to
attach, so PCIe preflight correctly refused publication without USB test writes.
That run is unqualified. It exposed missing legacy singular `gpio` reference
renumbering for two existing base regulators; the composer now preserves their
firmware-GPIO provider closure and tests that case. The separately corrected
native source then attached real OFW gpiobus and
published GPIO/MIP readiness. Its next cold run exposed a phase-5 model-profile
mismatch before PCI publication: the generic QEMU root default was Gen4/x32 with
fixed Target Link Speed 4, so the native 16-bit Gen2 write read back as 4.

The private root instance now uses QEMU's existing 5 GT/s and x4 properties before
realization. Its genuine capability is `0x300442`, negotiated link status remains
`0x2011` (Gen1/x1), and generic target speed is 2. The exact native phase-5 update
is now a required qtest. Shared root classes, configuration write masks and
negotiated status are unchanged; general TLS writability or retraining is not
claimed. The original 4/request-2/read-4 negative remains preserved. Baseline
fixed-clock warnings are not attributed to this model.

Cross-generation controls additionally migrated both qualified old `08d508` PCIe
profiles into this binary: disconnected reset/window/config-absence state, and
connected pending real xHCI event/INTx followed by another translated NoOp.

The next native run reached domain 2, a genuine Gen1/x1 link and xHCI probe,
but the first CAPLENGTH byte read aborted after the native allocator placed the
16 KiB BAR at legal PCI address zero. QEMU's existing raspi4 machine class
rejects zero BAR addresses. An actual AArch64 LDRB reproduces that failure on
the retained binary, while the nonzero control returns `0x40`. The separate
`raspi4b-pcie2-contract` class enables the existing PCI capability without changing
PCI core code, existing machine classes or driver allocations. Its new tests
require the exact native zero BAR and bridge window, actual CPU byte access,
real USB payload DMA and IRQs, and migration continuation at address zero.


The first added USB test exposed a fixture assumption: the reused INTx helper
required the interrupt to be low after ERDP acknowledgement, although advancing
the event ring can immediately generate another MSI edge. The retained failure
is followed by a corrected edge-aware fixture and a clean full rerun. No model
change was needed. The earlier R6 receipt remains historical.

Native frontend/provider/USB acceptance remains pending with the separately built
kernel. Physical BCM2712 reset/calibration, 54 MHz operation, Gen2x4 RP1 link,
UBUS behavior, MIP delivery and cache-coherency gates remain open.
