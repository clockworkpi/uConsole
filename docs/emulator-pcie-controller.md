# BCM2711 disconnected PCIe controller profile

The `raspi4b` model includes a controller-only profile at `0xfd500000`.
It allows firmware to reset the controller and discover that no PCIe device
is connected while booting from SD. It does not provide a PCI bus, endpoint,
xHCI, MMIO/DMA translation, MSI delivery from an endpoint, PHY/MDIO or link
timing. This does not qualify PCIe on a physical Pi 4 or any Pi 5/RP1 path.
The ordinary Linux FDT fixture still disables its PCIe node.

## Defined behavior

- The root-complex strap is set; PHY-up and data-link-active stay clear.
  Guest writes cannot create a link. The explicit controller revision profile
  is v3.20; it is not inferred from the host or a physical board inventory.
- Bridge reset and PERST have independent readback. Asserting bridge reset
  clears programmed control/window state and interrupt banks. Control/window
  writes are ignored while bridge reset is held. Releasing reset does not
  connect a device. Power-on/system reset holds both resets asserted.
- Known control/window registers retain programmed values. These values do
  not activate an unimplemented data plane. Every PCI configuration read
  returns all ones, including vendor/device identity; writes do not invent
  devices. The indexed configuration selector has masked readback.
- Controller and MSI interrupt banks have software set, write-one-clear,
  mask-set and mask-clear operations. Status registers are read-only. Outputs
  are routed through GIC SPI 147 and 148. These software controls test wiring;
  there is no endpoint generating an MSI transaction.
- Unsupported control/PHY offsets return an MMIO transaction error rather
  than silently reading zero. The model has no blanket RAM or unimplemented
  device mapping over the controller aperture.
- Migration retains reset, control/window, selector, interrupt status and masks
  and recomputes IRQ levels after load. This is same-model migration, not a
  claim of compatibility with old snapshots lacking the controller.

The power-on/reset and disconnected behavior are an explicit simulator profile.
The register layout and masks are based on the
[Linux v6.12 controller definitions](https://github.com/torvalds/linux/blob/v6.12/drivers/pci/controller/pcie-brcmstb.c),
[Linux BCM2711 interrupt binding](https://github.com/torvalds/linux/blob/v6.12/arch/arm/boot/dts/broadcom/bcm2711.dtsi),
and the [pinned EDK2 constructor](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Library/Bcm2711PciHostBridgeLib/Bcm2711PciHostBridgeLibConstructor.c).
Those sources do not establish hardware-equivalent timing or reset defaults.

## Validation

```sh
python3 tools/test_emulator_pcie.py --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The test applies the firmware's reset and window-programming sequence, checks
that config identity and link remain absent, exercises both IRQ banks through
the actual GIC pending inputs, and uses real QEMU migration with resets both
held and released. It then verifies bridge and system reset cleanup.
This remains a controller-only test. A firmware retry is recorded separately in
the [Pi 4 UEFI checkpoint](emulator-pi4-uefi.md).

On 2026-11-07, the Linux ARM64 system-toolchain build of pinned QEMU 10.2.4
passed both controller test cases (including real migration), the RNG200
reset/IRQ/migration regression, and all 11 source-cache/extraction tests.
The tested binary SHA-256 is
`7dd3e727bdb8a1d8df00ee7aa17124e1b4967afe776d9218b59a2fbb83c51f9e`.
A first build placed the region at the wrong low-peripheral offset; the initial
physical-address MMIO assertion caught that error. The corrected map is CPU
`0xfd500000`, GPU `0x7d500000`. The failed control was retained privately.
