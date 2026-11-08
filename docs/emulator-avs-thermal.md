# BCM2711 AVS temperature status

The Pi 4 UEFI/ACPI control reached the FreeBSD kernel after the xHCI and
framebuffer fixes, then panicked while evaluating the ACPI thermal zone's
`_TMP` method. A fresh run on the preserved pre-sensor binary captured:

| Register | Observation |
| --- | --- |
| ESR_EL1 | `0x96000010`, synchronous external data abort |
| FAR_EL1 | `0xffff0000403af200` |
| ELR_EL1 | `0xffff00000005b048` |
| TTBR1_EL1 | `0x312bc000` |
| TCR_EL1 | `0x14b5103510`, 4 KiB pages, 48-bit upper translation |

Reading the actual translation tables through QMP physical memory accesses
resolved the faulting virtual address to **CPU physical `0xfd5d2200`**. Its
final page descriptor was `0x00600000fd5d2713`. The address matches
[`THERM_SENSOR` in EDK2's BCM2711 definitions](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Silicon/Broadcom/Bcm27xx/Include/IndustryStandard/Bcm2711.h).
The relevant [Raspberry Pi DSDT](https://github.com/tianocore/edk2-platforms/blob/08566230c98729eb9760211e3111c7b4c391ccbf/Platform/RaspberryPi/AcpiTables/Dsdt.asl)
reads the 32-bit status and decodes the low ten bits into a temperature.
This identifies a missing emulated register; disabling the ACPI thermal zone
would conceal that missing device.

## Model contract

The [model](../Code/patch/qemu/bcm2711_avs_thermal.c) maps the read-only AVS
temperature status at CPU `0xfd5d2200`, GPU `0x7d5d2200`, using the existing
BCM2711 low-peripheral alias. It does not add a RAM window at the faulting
address. The implemented eight-byte region contains a single 32-bit status
register at offset zero; the adjacent word is not a second sensor register.

The Linux v6.12
[AVS thermal driver](https://github.com/torvalds/linux/blob/v6.12/drivers/thermal/broadcom/bcm2711_thermal.c)
accepts validity bit 10 or 16 and decodes bits 9:0. The
[BCM2711 FDT calibration](https://github.com/torvalds/linux/blob/v6.12/arch/arm/boot/dts/broadcom/bcm2711.dtsi)
uses `temperature_mC = 410040 - 487 * raw`, also matching the EDK2 formula.
The model returns validity bit 10 and the nearest quantized ten-bit code.

The mutable QOM property
`/machine/soc/peripherals/avs-thermal.temperature-millicelsius` accepts
integer requests from -40,000 to 125,000 mC. Invalid values are rejected
without changing the reading. The default request is 42,000 mC, which
quantizes to raw 756 and a Linux-decoded 41,868 mC; the status word is `0x6f4`.
Quantization can differ from the requested value by up to 244 mC. Reset
restores the default request. Migration retains the injected request and
rejects out-of-range incoming state.

Word writes to the status register are ignored. Unsupported widths,
unaligned accesses and reads of the adjacent hole return a memory
transaction error and no valid sensor data. They cannot rewrite the host
temperature property. The model does not implement AVS control registers,
oscillator behavior, thermal inertia, load-dependent heating, cooling or
sensor-failure physics. A chosen temperature is a test input, not a measured
physical board temperature.

## Validation

```sh
python3 tools/test_emulator_avs_thermal.py \
  --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The regression uses real QEMU MMIO and a real BCM2835 DMA transfer to read
the **GPU** alias, rather than incorrectly treating a GPU address as a CPU
physical address. It checks the default and boundary temperatures,
calibration quantization, monotonic readings, invalid injections, read-only
behavior, unsupported widths/alignment/hole accesses, reset and real QEMU
migration at both injection boundaries.

These controls passed independently on the built QEMU binary with SHA-256
`bb335a6d852cf52385a5a46aced84fd1c6c98f329b4db937ec790bcd11ffe5b2`.
The CPU and actual GPU-DMA reads agree; real migration retained both -40,000
and 125,000 mC injections and reset returned each target to its default.

The existing Linux FDT fixture still disables the thermal node. The register
and migration controls alone do not qualify the Linux guest driver. An actual
Linux control and a new unchanged-firmware FreeBSD ACPI/USB run are required
before claiming those guest paths pass. Neither can establish physical
temperature accuracy or Pi 5/RP1 support.
