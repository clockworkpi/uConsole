# SDHCI stop completion and SD write-count encoding

The Pi 4 UEFI experiment exposed a controller-state defect after real SD reads.
With the secure GIC firmware profile and the SD card attached to EMMC2, unchanged
pftf UEFI v1.53 read sectors 0 and 1 using CMD17, then the GPT entry array using
an unbounded CMD18 transfer. It issued an explicit CMD12 abort/R1b to stop that
transfer. The card accepted CMD12 and moved from sending-data to transfer state,
but QEMU's host kept DATA_INHIBIT, DOING_READ and DATA_AVAILABLE set. Firmware
then polled PRESENT_STATE instead of issuing the next storage command.

The source only completed host state for bounded/automatic stops; explicit
CMD12 did not run the common completion path. This was not an absent-card,
missing GPT or console-input diagnosis. Earlier `query-blockstats` entries at
the drive-device level showed zero counters, but the command/card trace proves
actual sector reads. Those counters alone were insufficient evidence.

## Change

The local [QEMU patch](../Code/patch/qemu/sdhci-manual-cmd12.patch) extracts the
existing data-completion status/IRQ cleanup and invokes it after a successful
explicit CMD12 with command type ABORT. It discards any prefetched/partial FIFO
and clears block-gap continuation state. It does not send a second AutoCMD12,
change the card response, synthesize sector data or clear active transfers on
unrelated command-only requests. The existing bounded/AutoCMD12 path uses the
same completion cleanup. Completion and DATA software reset also cancel a
pending ADMA transfer timer, so its callback cannot continue an aborted phase.

SDMA System Address writes now resume data only while DMA is selected and
DATA_INHIBIT still marks an active SDMA phase. Remaining block count and address
register readback are retained after abort; a later address-register write
cannot turn those retained values into a new transfer. A separate existing
boundary-resume defect also needed repair: the synchronous SDMA engine leaves
DOING_READ/WRITE set while paused at a buffer boundary, but the old outer
address-write guard rejected that state. The guard now permits active SDMA
boundary resumes without changing those status bits or starting an inactive
engine.

This changes generic SDHCI behavior, so Pi 4 firmware progress alone is not an
adequate regression gate. Direct Linux and FreeBSD FDT SD boots must also run
on the exact resulting binary.

## Reproduction and validation

```sh
python3 tools/test_emulator_sdhci_stop.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The test initializes a real emulated SDSC card through MMIO, reads known data,
performs unbounded multi-block PIO reads and explicit CMD12, aborts a partially
consumed FIFO, then reads a different block to detect stale FIFO state. It also
checks bounded AutoCMD12, complete/partial PIO writes, actual GIC transfer IRQ
and W1C, non-abort command isolation, data reset and system-reset recovery.
It uses a newly created private image and does not touch an operator's media.

Before the patch, this test reproduces the same failure after actual data:
`Manual CMD12 left data/FIFO state: 0x1ff0a02`. Firmware's observed value was
`0x01ff0a06`; both retain the inhibited read phase and unread prefetched data.
The [pinned upstream implementation](https://github.com/qemu/qemu/blob/v10.2.4/hw/sd/sdhci.c)
provides the source baseline. The patch is local, not an upstream-merged fix.

Firmware, image identity and later bootstrap results belong to the
[UEFI checkpoint](emulator-pi4-uefi.md). This fix does not qualify physical SD
routing, EEPROM/VideoCore boot, native PCIe/GENET or a final distribution.

The independent generic PCI SDHCI fixture exercises DMA-capable hardware (the
Pi 4 profile advertises no DMA):

```sh
python3 tools/test_emulator_sdhci_adma_stop.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

On the intermediate PIO-only repair, all four DMA controls reproduced separate
failures: pending ADMA continued modifying guest memory after CMD12 or DATA
reset; a post-abort System Address write restarted SDMA; and a genuine active
SDMA boundary address write failed to resume the next blocks. With the complete
repair, all four pass, including transfer of the next eight actual blocks in
the positive boundary-resume control.

On 2026-11-08 the final Linux ARM64 system-toolchain QEMU 10.2.4 build passed
the PIO and DMA suites, RNG200/PCIe/GENET reset/IRQ/migration regressions, GENET
actual-CPU unsupported-MMIO controls and all 11 source-cache/extraction tests.
The tested binary SHA-256 is
`7fe73e7a5b2cf80a05ac373687b92682a728e0b2ce692045e76161c013d366d5`.
Full FreeBSD and Linux guest regressions are recorded with their exact-artifact
results; these focused controls alone do not establish an accepted boot profile.

## Subsequent ACMD22 wire-encoding defect

After the SDHCI repair, firmware completed its first 64 KiB NVRAM-file write
and faulted while attempting the next write from `0x100003b6000`. Fresh
hardware-breakpoint tracing showed that the first MMC write entered with
buffer `0x3b6000`, and the pointer remained correct before and after every LED
callback. The second write already entered with the invalid high address.
The caller's remaining length was `0xffffff0000010000`, matching a 1 TiB
subtraction rather than register or LED/mailbox corruption.

The card model copied native-endian `blk_written` directly into ACMD22's data
payload. After 128 completed blocks, a little-endian host supplied wire bytes
`80 00 00 00`; the firmware correctly byte-swapped the received value and
interpreted it as `0x80000000` blocks. Its subsequent multiply by 512 advanced
the buffer by exactly `0x10000000000` bytes and underflowed the remaining
length. The actual caller instructions (`REV`, `MUL`, buffer add) and the
invalid incoming argument were captured before the fault.

The [focused SD card patch](../Code/patch/qemu/sd-acmd22-wire-endian.patch)
serializes the count as big-endian bytes before copying it into the card's
transmit buffer. It does not change counters, firmware arithmetic, virtual
mappings or guest register state. Linux's
[ACMD22 consumer](https://github.com/torvalds/linux/blob/v6.12/drivers/mmc/core/block.c)
also converts this four-byte network-order count with `ntohl`. The
[pinned QEMU source](https://github.com/qemu/qemu/blob/v10.2.4/hw/sd/sd.c)
is the baseline; this remains a local patch.

The real-card PIO test now writes 1, 2 and 128 complete blocks, adds a partial
next block, stops the transfer and reads ACMD22 through the controller. It
checks the exact wire bytes, excludes the partial block from the count, and
reads back the last completed block. Before the encoding fix, its first case
observed `01 00 00 00` instead of `00 00 00 01`, independently reproducing the
protocol defect without depending on firmware's exception handling.

The encoding-fix build passed the exact wire-count cases (1, 2 and 128), the
full PIO stop/write/reset suite, all four DMA lifecycle controls, GENET's actual
CPU/MMIO/IRQ/migration controls, PCIe/RNG200 regressions and all 11 source-cache
tests. Its Linux ARM64 QEMU binary SHA-256 is
`9fa618ecfa9a630c659acc8123a463bfc8fa99f16c26c2373b203b69721d931b`.
The unchanged-firmware retry reached the FreeBSD loader, saved and read-back
verified its ACPI snapshot and raw-slot state, and began loading the kernel.
That progress establishes removal of the NVRAM-write blocker; kernel/init and
full distribution acceptance require their own subsequent records.
