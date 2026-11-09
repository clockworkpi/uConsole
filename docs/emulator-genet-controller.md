# BCM2711 GENET disconnected control profile

The Pi 4 simulator maps a functional GENET control profile at CPU
`0xfd580000` / GPU `0x7d580000`. It supplies firmware-visible reset, MAC,
MDIO/PHY, interrupt and descriptor-configuration behavior while booting from
SD. Its PHY has no carrier. It has no NIC backend or guest-memory DMA engine,
never consumes descriptors or completes a packet, and does not establish a
native Ethernet result. The ordinary Linux FDT fixture still disables GENET.
This is unrelated to the Cadence GEM Ethernet controller in Pi 5/RP1.

## Supported behavior and limits

- The model identifies the GENET v5 register layout (major encoding 6).
  Known control words have readback; revision and packet counters are read-only.
  Unknown/reserved MMIO offsets cause a transaction error. There is no blanket
  RAM mapping across unsupported register holes.
- RBUF/TBUF flush controls operate on empty buffers in this profile. UMAC
  software reset clears MAC/filter configuration; it preserves the separate
  DMA descriptor/configuration SRAM. MAC0/MAC1 support station-address readback,
  with only the low 16 bits of MAC1 writable. MIB counters remain zero because
  no packet traverses the model.
- The explicit PHY control subset at MDIO address 1 uses BCM54210-compatible
  identity and standard Clause 22 registers plus Broadcom AUX/clock shadow
  selectors needed by EDK2. The identity is a simulator profile, not an
  inventory claim about a physical Pi's PHY revision. Other addresses return
  `0xffff` with the MDIO read-failure flag and error interrupt.
- MDIO commands complete immediately; busy clears and done/error status is
  latched. PHY software and external-reset controls restore PHY defaults.
  BMCR reset/restart commands self-clear in this functional profile. Carrier
  and autonegotiation completion stay clear; status/partner-register writes
  and MAC/OOB configuration cannot invent a link. PHY timing, signal levels,
  vendor analog behavior and health are outside the profile.
- Both interrupt banks implement software set, W1C clear and mask set/clear.
  Their actual GIC outputs are SPI 157 and 158. MDIO raises the first bank.
  Software-forced IRQs test routing, not a real RX/TX completion.
- The two 256-entry descriptor SRAMs and known queue/control words retain
  software configuration. Enabling an engine does not access packet addresses
  or advance hardware producer/consumer indices. Ownership bits stay unchanged,
  no guest memory is read/written and no completion interrupt is generated.
  Invalid packet addresses are safe in this disconnected profile; successful
  network traffic or DMA fault handling is not implemented or claimed.
- System reset clears control, descriptor/ring state, IRQ banks and PHY state.
  Same-model migration retains those states and recomputes IRQ levels. Malformed
  shadow selectors are rejected. Old snapshots lacking this device are outside
  this migration contract.

Register layouts and masks are based on the
[Linux v6.12 GENET definitions](https://github.com/torvalds/linux/blob/v6.12/drivers/net/ethernet/broadcom/genet/bcmgenet.h),
[PHY definitions](https://github.com/torvalds/linux/blob/v6.12/include/linux/brcmphy.h),
and [pinned EDK2 GENET code](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Drivers/Net/BcmGenetDxe/GenetUtil.c).
These references establish software register contracts, not hardware-equivalent
reset timing or analog/network behavior.

## Validation

```sh
python3 tools/test_emulator_genet.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The test applies EDK2's reset/MAC setup; exercises Clause 22 access, absent PHY
addresses, no-link/partner behavior and shadow registers; observes both IRQs
through the real GIC pending inputs; configures owned descriptors with invalid
packet addresses and checks that neither memory nor completion state changes;
and migrates the actual QEMU device state before testing reset cleanup.

A separate original AArch64 EL3 payload executes actual CPU MMIO accesses.
Mapped controls must complete; reserved/unsupported offsets must enter the
exception vector with synchronous external-abort status and the exact fault
address. In the EL3 control, ESR is `0x96000210`; the firmware's EL2 fault had
`0x96000010` (different EA bit). This avoids mistaking Qtest's zero result on
an unassigned access for a demonstrated guest-visible fault.

The unchanged pftf UEFI experiment and any later loader progress are recorded
in the [Pi 4 UEFI checkpoint](emulator-pi4-uefi.md). Physical Ethernet, RX/TX/DMA,
full RAM, installed boot and final distribution acceptance remain separate.

On 2026-11-08 the Linux ARM64 system-toolchain build of pinned QEMU 10.2.4
passed these GENET checks, both actual-CPU positive controls and both reserved
MMIO fault controls. The RNG200 and PCIe reset/IRQ/migration regression tests
and all 11 source-cache/extraction tests also passed. The final binary SHA-256
is `cf65cb291e517a6a3db41bd3db5990006da003f7eb5d90fb66ae901be1f30eec`.
These results do not qualify physical Ethernet or a GENET packet data plane.
