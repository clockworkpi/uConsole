# Pi 4 memory simulation profiles

`raspi4b` retains its existing 2 GiB default on 64-bit hosts. Two explicit
64-bit-host profiles model larger capacities:

| Machine | Board revision | Backing capacity | CPU and PCIe RAM banks |
| --- | --- | --- | --- |
| `raspi4b-4g` | `c03115` | 4 GiB | `[0, fc000000)` |
| `raspi4b-8g` | `d03115` | 8 GiB | `[0, fc000000)`, `[100000000, 200000000)` |

Addresses in this document are hexadecimal. The 64 MiB peripheral hole
`[fc000000, 100000000)` has no background RAM. Implemented devices remain
accessible there; unsupported addresses fault. CPU accesses and PCIe inbound
DMA use the same physical RAM container, so device DMA cannot bypass the
hole by reaching the raw backing. Offsets in the backing equal physical
addresses. GPU cache aliases expose the low 1 GiB in each of their four bus
windows.

The default VideoCore reservation remains 64 MiB. It remains physically
backed, including framebuffer accesses, and is excluded from the OS's FDT
memory banks. The legacy 32-bit mailbox reports the low ARM bank
`[0, 3c000000)` and the VideoCore range `[3c000000, 40000000)`; it does not
truncate the full installed capacity into a 32-bit size. The board revision
and FDT describe installed/usable memory separately.

The enrolled physical 8 GiB Pi 4's retained Linux FDT uses revision `d03115`,
two address cells and one size cell. Its usable ranges are
`[0, 3b400000)`, `[40000000, fc000000)`, `[100000000, 180000000)` and
`[180000000, 200000000)`. Its VideoCore reservation is **76 MiB**, so the
simulator's default low bank is 12 MiB larger. These profiles preserve the
existing simulator reservation; they do not claim to reproduce every native
firmware reservation. Linux CMA is reusable RAM, not a permanent hole.

The QEMU boot callback emits the bank above 1 GiB without crossing the
peripheral hole. With one size cell it splits the upper 4 GiB into two 2 GiB
banks; with two size cells it emits a single upper bank. It preserves root
cell widths, checks every added memory node/property and rejects addresses
that cannot be represented. Changing only the revision or `-m` would not
supply this memory-map behavior; a conflicting `-m` remains an error.

## Model controls

Run against the selected build:

```sh
python3 tools/test_emulator_pi4_memory.py \
  --qemu build/emulator/qemu-build/qemu-system-aarch64 \
  --output /a/fresh/private/directory
```

The fixture executes original AArch64 code for RAM writes/readback,
low/high-bank isolation, peripheral-hole faults and exact capacity faults,
and checks actual ESR/FAR. It verifies revision and mailbox responses on all
three profiles. Hermetic DTBs pass through `arm_load_dtb`; QMP dumps verify
both size-cell widths and refusal of an unrepresentable upper address.

Real xHCI command/event rings exercise nonidentity PCI translation
`PCI 400000000 -> ARM 0`, with an 8 GiB inbound window. Rings, device
contexts and disk payloads above physical 4 GiB use PCI DMA addresses above
20 GiB, including every pointer's high half. Controls verify bank-boundary
NoOps, native INTx, actual 128 KiB USB reads/writes/readback and continuation
after migration with a pending storage interrupt. A valid command seeded in
hidden backing inside the peripheral hole must remain inaccessible. The
exact 8 GiB boundary must also reject DMA.

Active-device migration exposed an ordering defect: xHCI reconstructs its
contexts through DMA during `post_load`, before the host's default-priority
state restored its inbound window. The controller now uses QEMU's
`MIG_PRI_IOMMU` priority, after PCI bus state and before PCI devices. The
same stream passed when only the target window was provisioned beforehand;
the repaired fixture requires a fresh, unconfigured target and rejects a
host-controller error. Existing NoOp-only migration did not cover this case.

The test RAM backends explicitly select `reserve=off`, preserving their full
capacity while avoiding a 16 GiB host swap reservation for two migration
peers on small CI runners. The normal board profiles keep QEMU defaults.
The extracted Linux ARM64 archive ran the full memory fixture with sampled
combined process RSS of 140.3 MiB; zero-page migration did not materialize
8 GiB of resident RAM. The release workflow runs the same packaged fixture
on Linux x86_64, Linux ARM64 and macOS ARM64.

These are host-backed simulator controls at selected bank boundaries, not
an exhaustive RAM walk or physical DRAM/cache/interconnect qualification.

## Firmware limit control

QEMU SHA-256 `430f85ef0819672ce21b2012476fea3abac39cd7d9598b31dff89df589883f34`
passes the new controls, all nine existing Pi model suites, the 2 GiB Linux
control's 10 checks and the FreeBSD FDT control's 15 checks.

On `raspi4b-8g`, two private copies of the pinned pftf v1.53 firmware changed
only a previously erased NVRAM record for `RamLimitTo3GB`, under ConfigDxe's
GUID `cd7cc258-31db-22e6-9f22-63b0b8eed6b5`. The firmware executable code and
original FD were unchanged. The loader read the variables back with
`NV,BS,RS` attributes and captured actual EFI `memmap` output before boot:

| Firmware setting | Loader memory-map observation | FreeBSD ACPI/USB result |
| --- | --- | --- |
| Limit on (`1`) | Ordinary RAM ends at `c0000000` | All 19 boot/init/durable-diagnostic checks pass |
| Limit off (`0`) | Adds `[c0000000, fc000000)` and `[100000000, 200000000)` | Native xHCI allocates rings above 4 GiB and cannot reach USB root |

The source kernel/image has no `hw.physmem` override. The EFI map establishes
that the firmware setting takes effect independently of a kernel cap.

The cap-off failure is a real DMA-boundary constraint: the unchanged
[firmware PCI host constructor](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Library/Bcm2711PciHostBridgeLib/Bcm2711PciHostBridgeLibConstructor.c)
programs BAR2 `11/0`, a 4 GiB inbound identity window. Live registers show the
same values while the kernel programs xHCI CRCR `1_037f8f01`, DCBAAP
`1_037f9000` and ERST `1_037f8000`. xHCI reports Host Controller Error and
repeated USB timeouts. The simulator does not silently widen that programmed
window. Correct OS DMA constraints or a separately qualified firmware inbound
configuration remain necessary for full-memory ACPI/USB acceptance. The
independent 8 GiB-window high-DMA fixture passes without this restriction.

A separate disposable cap-off experiment with the existing
`hw.usb.xhci.dma32=1` tunable also passes all 19 ACPI/USB boot checks while
retaining full 8 GiB memory visibility. This confirms DMA reachability as the
cause; a 32-bit constraint still permits 3–4 GiB, so it does not implement the
firmware ancestor `_DMA` resource's stricter `[0, c0000000)` contract. A valid
live XSDT references the captured `RPI4XHCI` SSDT (580 bytes, checksum valid),
which supplies that resource independently of the source ASL.

All image experiments use regular-file copies and overlays. No physical
board or removable device was written or restarted. The VideoCore/EEPROM
boot chain remains outside this simulation profile.
