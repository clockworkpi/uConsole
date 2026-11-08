# Optional BCM2711 GENET network profile

The default Raspberry Pi 4 simulator keeps the previously qualified disconnected
GENET control profile. Enable the separate real network model with:

```sh
-global bcm2838-peripherals.genet-network=on \
-netdev user,id=genet0,restrict=on,hostfwd=tcp:127.0.0.1:2222-:22 \
-global bcm2838-genet.netdev=genet0 \
-global bcm2838-genet.mac=52:54:00:12:34:56 -nic none
```

This uses native GENET descriptors and MMIO, QEMU's packet backend, and the guest's
GENET/MII drivers. It does not substitute a PCI or USB network adapter. The direct
kernel FDT profile retains `brcm,bcm2711-genet-v5` only when this option is enabled.
GENET remains FDT-only in the selected FreeBSD kernel; ACPI bootstrap success does
not establish native GENET attachment.

## Upstream provenance

The implementation is backported from official QEMU commits, ending at
[`b63903e3964094201730144832c01d8afecffdea`](https://github.com/qemu/qemu/commit/b63903e3964094201730144832c01d8afecffdea):

- `637895d19c6ef8ab6689a741d299939133ce2406`: device skeleton/integration.
- `0717537ce523f865f0e3fe51077bb74112272440`,
  `74bbe6e3ef00826745952a52c2136a5b6c743b7f`,
  `a7b20972f3161fc230759dc7600dbe3ed78506a8`: register layouts.
- `c4f3cc72bec8f294e5e53920e65bc473147385b9`: register operations.
- `5019dc4e7c654d81b53830776295db5772208177`: MDIO.
- `bf5214feee1f7f572b08808cc61d122c36627a8b`: TX.
- `e5a82573451af451fa051a5ada521def8d6fc19f`: RX.
- `b63903e3964094201730144832c01d8afecffdea`: migration.

Pinned C Git blob: `53bba99b38a62557970423de967ac317e3fdb8dc`;
header blob: `2420711a29c05ecc7cf1ec2e03b227dbd844fa3c`.
The original GPL-2.0-or-later copyright/license notices are retained.

Compatibility changes use QEMU 10.2 header locations and distinct C state/object
names so both profiles can coexist. The backend, register, filter, MDIO and TX paths derive from upstream code;
the bounded RX path adds the ownership/format checks described below. Board integration selects exactly one realized GENET child.

## Address, interrupt, PHY, and DMA contracts

The controller occupies CPU `0xfd580000` / VideoCore `0x7d580000`. Default and
priority interrupt outputs use native SPI 157/158 (GIC architectural IRQ 189/190).
Both interrupt banks implement status, mask, mask-clear, and W1C operations.

The `phy-addr` property selects one MDIO address (default 1 for Pi4B; use
`-global bcm2838-genet.phy-addr=0` for a CM4 FDT declaring PHY 0). Values outside
0 through 31 are rejected; migration requires identical source/destination straps.
The selected PHY has ID `600d:84a2`; other MDIO addresses fail reads. Link and negotiated
partner abilities follow the actual attached backend's link state. With no
backend there is no carrier. Clause22 identity/status/partner registers are read
only, and BMCR reset completes synchronously. Broadcom AUXSTS reports the same
backend-derived 1000BASE-T full-duplex link result as the standard capability and
partner registers. PHY electrical behavior is not
simulated.

Descriptor buffer addresses are CPU physical addresses in the board's RAM-only
address space, including shared RAM above 4GiB on the 8GiB board. The peripheral
hole `0xfc000000..0xffffffff` and out-of-range addresses are not DMA RAM. This
preserves the qualified CPU/PCIe RAM mapping and prevents network descriptors from
writing peripheral registers. Failed DMA does not produce a successful packet
completion interrupt.

Additional bounded fixes validate descriptor RAM geometry/index distance and
16-bit counter wrap. RX checks available descriptors before overwriting an owned
buffer. RBUF/TBUF status-block enable and RX alignment are honored; only initialized
packet/status/CRC bytes are written. Reset deasserts both IRQs, and migration
restores register/PHY state and recomputes IRQ levels.

## Scope and evidence

`tools/test_emulator_genet_network.py` connects a private socketpair backend and
programs real rings. It does not require a privileged interface or external
network service. It covers packet bytes, high-RAM DMA, both default and priority
rings, repeated wrap, split TX descriptors, counter rollover, IRQ masking/W1C,
malformed rings, full RX ownership, invalid DMA, reset, and migration continuation.

This profile supports ordinary Ethernet frames up to 1518 bytes. Jumbo/scatter RX
and hardware partial RX checksum offload are not implemented. The RX checksum
status reports no verified checksum so the guest validates in software. The model
retains upstream limitations outside these bounded controls; passing packet tests
alone does not establish complete hardware fidelity or actual OS networking.

Actual Linux/FreeBSD driver attachment, DHCP, bidirectional application transfer,
interrupts, and clean shutdown must be qualified separately against the exact
published binary. No physical Pi networking has been tested by this work.

The first frozen network candidate is generation
`741bb050c6356e2bc2b4a38bded4460e6fc8e5a2ffaf5240cd3957600c897807`,
`qemu-system-aarch64` SHA256
`9af365a27bfdbae57792666ce1d0d7cd136379ed6a8eabd1a0ab90009c6dbfe6`.
The network packet/PHY suite and all ten existing hardware model suites passed on
that exact binary. Source-cache safety tests also passed (11 cases). Local retained
receipts are under `/home/jkh/daemonarchy-debug/genet-upstream-evaluation-20261108/`:
`packets-build5.log`, `packets-build5/`, and `regressions-{0,1,2}.log`.

## TX assembly across producer updates

`tools/test_emulator_genet_tx_staging.py` first reproduced the upstream failure on
that baseline: a 114-byte frame split between producer updates emitted only its
final 20 bytes. The correction keeps a bounded 4095-byte assembly buffer, length,
active-SOP flag and captured status-block mode for each of the 17 rings. A consumed
prefix survives reuse of its descriptor and original guest buffer. Only valid EOP
emits a packet and raises packet completion; a new SOP abandons an unfinished
frame. DMA failure, invalid ring geometry, overflow, explicit UMAC TX flush and
controller reset clear assembly. An orphan continuation cannot splice stale bytes
into another frame. Disabling DMA pauses assembly without treating it as a flush.

VMState version 2 saves all assembly buffers and metadata and validates restored
lengths/modes before use. Version 1 input loads empty assembly because the old model
never saved its consumed prefix; an orphan tail is discarded until a fresh SOP,
rather than fabricating unavailable data. Version 2 streams require a new model on
the destination; the default disconnected profile retains its existing VMState.

The staging fixture exercises separate doorbells, one-entry descriptor reuse,
16-bit index rollover, interleaved rings, error/flush/reset recovery, a migrated
unfinished frame after guest buffer reuse and changed global TBUF mode, and actual
version 1-to-version 2 migration. It disables QEMU's generic post-migration RARP
announcements (`announce-rounds=0`) so backend quiescence checks observe controller
packets. An observed 60-byte EtherType 8035/opcode 3 RARP was retained separately; it
was not a GENET premature packet.

The new frozen candidate is generation
`74ba689fc664ad57d826fc4b1339005befb42eb286fb6680506a0fa06c548065`,
system binary SHA256
`bb79102f8cdebdd0da7f0678507a9281f496934f1089d8b1ab4b7f8f20f85ca6`.
Staging and existing packet/PHY suite receipts are `stg7-freeze.log` and
`packets-build7.log` in the same local evidence directory. The baseline failure is
`staging-before.log`; the unrelated RARP capture is `stg7-rarp.log`.

Hardware MIB counter fidelity, full PHY electrical/forced-mode behavior, jumbo RX,
and RX checksum verification remain outside qualification. Native OS acceptance
and physical performance remain separate from these model controls.
