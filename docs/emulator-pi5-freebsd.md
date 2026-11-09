# Pi5 / BCM2712 FreeBSD multi-user SD model

The separate `raspi5b` machine boots the FreeBSD-based Daemonarchy kernel to
multi-user, runs the standard `/etc/rc`, and provides a real serial getty/login.
It has four Cortex-A76 CPUs with BCM2712 affinities `0`, `0x100`, `0x200`,
`0x300`, PSCI, GIC-400, the SoC debug PL011, RNG200 and the native BCM2712 SD
controller at their Pi5 physical addresses. It does not inherit the BCM2711
machine or use a Pi4 device tree. The initial enrolled board tree is the
captured BCM2712 D0 Pi500 tree; C1 pin layouts are not qualified by this result.

This is an SD/serial digital bootstrap model. VideoCore/EEPROM firmware startup,
RP1 USB/Ethernet/display/input, PCIe electrical behavior, pad voltages and analog
clock/timing behavior are not implemented or qualified here. Use the existing
selected PCIe2 contract separately for its host/MSI/DMA tests. It is not a
replacement for physical boot acceptance or an installed desktop distribution.

## Inputs and boot path

The boot path is QEMU's AArch64 Linux boot ABI into FreeBSD's existing
`LINUX_BOOT_ABI` support. `prepare_pi5_boot.py` converts the ELF load segments
into an Image, adding the AArch64 Image header in the unused ELF header area.
The kernel's executable bytes are preserved. A native FreeBSD EFI loader and
Pi5 U-Boot/firmware handoff are separate paths, not part of this direct boot.

The derived FDT preserves the enrolled Pi5 CPU, SoC bus translation, GIC, SD,
UART, clock, pin and regulator bindings. It explicitly disables unimplemented
devices, omits unused fixed-clock placeholders, sizes RAM and selects the debug
UART. Its manifest records the input/output SHA256 identities and every changed
property/removed clock node. Unsupported clock nodes are omitted because the
FreeBSD fixed-clock probe also attempts disabled placeholders with no frequency.

The machine accepts 2, 4 and 8 GiB, with 8 GiB default. All RAM banks appear as
`reg` tuples in one memory node: the FreeBSD direct-boot FDT parser reads that
node rather than collecting separate root memory nodes. The boot registers
have explicit MMIO/reset/migration state; SD card-detect reflects the attached
QEMU SD card rather than a constant insertion flag. Polled GPIO is the modeled
subset; general GPIO interrupt delivery is not claimed.

A fresh native GENERIC kernel is required. The BCM2712 SD card-detect consumer
must pass its actual FDT node to the OFW GPIO API. Earlier D520 kernels pass zero
and panic on the real Pi500 card-detect property. The correction is in
[daemonarchy-src PR77](https://github.com/jordanhubbard/daemonarchy-src/pull/77),
source `26afadf8a344b572515919baef9d9081defb37e2`, stacked on the earlier mapping
and PL011 fixes. The qualified development kernel's SHA256 is
`92ad4a467541c86d57673cbfb4a334995c8e51ec897d315e06a5df25442b47a0`.
The native compiler/bootstrap provenance remains recorded separately; an
installed compiler is not claimed as a source-qualified compiler product.

## Reproduce

Build isolated QEMU with `python3 tools/build_emulator_qemu.py --jobs 4`.
On Linux hosts with both Homebrew and system GLib tools installed, use a build
PATH whose `pkg-config`, GLib headers/libraries and `gdbus-codegen` agree. The
recorded Linux build used `/usr/bin:/bin:/usr/local/bin` first and no inherited
`LD_LIBRARY_PATH`/`DYLD_LIBRARY_PATH`.

On the allocated FreeBSD ARM64 worker, use a matching aarch64 ISO and native
kernel. Keep all staging and outputs outside source and the live root:

```sh
sudo sh tools/build_pi5_freebsd_root.sh \
  -i /path/to/matching-aarch64.iso -s ISO_SHA256 \
  -k /path/to/native-GENERIC-kernel -K KERNEL_SHA256 \
  -o /path/to/new-pi5-root
```

This writes a new regular UFS/GPT image; it refuses existing output directories.
It retains the actual base `/etc/rc`, init, getty/login and libraries. The private
VM has an empty root console password, no SSH service and no emulated NIC in
the test command. It is not a published general-purpose image.

Copy/decompress `pi5-multiuser.img.xz` to the simulator host. Obtain the enrolled
`bcm2712-rpi-500.dtb` (SHA256
`400b2da08abb8fd0ffd65085d1e0e1e6c6d01cadd27385403ea3a3fd5a1537f4`)
and the same native kernel:

```sh
python3 tools/prepare_pi5_boot.py \
  --dtb /path/to/bcm2712-rpi-500.dtb --kernel /path/to/kernel \
  --memory-gib 8 --output /path/to/new-boot-inputs
python3 tools/run_pi5_freebsd.py \
  --qemu build/emulator/qemu-build/qemu-system-aarch64 \
  --boot-inputs /path/to/new-boot-inputs \
  --disk /path/to/pi5-multiuser.img --memory-gib 8 \
  --output /path/to/new-run
```

The runner hashes its regular-file inputs, creates a fresh COW overlay, drains
the serial console continuously and bounds command frames. It logs in through
getty/login, checks four CPUs and full modeled RAM, init/services, native UFS,
the on-disk kernel hash and storage I/O, then requests reboot. Source image
invariance, disk synchronization and QEMU natural exit are checked separately.
Logs, commands, input hashes and individual guards are saved in `result.json`.
No physical disk is accessed.

For an interactive console using a separately created writable COW disk:

```sh
build/emulator/qemu-build/qemu-system-aarch64 \
  -M raspi5b -m 8G -kernel /path/to/boot-inputs/FreeBSD-Image \
  -dtb /path/to/boot-inputs/pi5-sd.dtb \
  -drive if=sd,format=qcow2,file=/path/to/disposable-overlay.qcow2 \
  -display none -serial stdio -monitor none -nic none
```

## Regression controls

```sh
python3 tools/test_prepare_pi5_boot.py
python3 tools/test_emulator_pi5_boot.py \
  --qemu build/emulator/qemu-build/qemu-system-aarch64
```

The model checks exercise actual MMIO masking, real SD-backend presence/absence,
CPU topology, random words, VMState migration, reset and unsupported RAM/CPU
refusals. They run against rebuilt and packaged QEMU in native package CI.
Those controls alone do not establish a guest multi-user boot; the native
FreeBSD console/storage run above is the runtime acceptance.
