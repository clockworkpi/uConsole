# CM4 / CM5 kernel for the April 2026 image

Source: <https://github.com/cuu/ClockworkPi-linux>, branch `rpi-6.12.y`,
commit `03436f4117533693d608b10eb4b3965987230b68` (Linux 6.12.62).
Use that exact revision, then apply `0001-remove-unused-panel-locals.patch`
from this directory in the kernel tree. The patch removes five unused local
variables without changing panel initialization or display behavior.

## Build

### Candidate USB audio drain fix

`0003-usb-audio-drain-period.patch` targets the pinned 6.12.62 source above.
During playback drain, reporting a period boundary while preparing the final
USB request can stop the endpoint before that request is submitted. The
candidate defers that notification to transfer retirement, preserving the
existing pending-request drain behavior.

Build a separate candidate module without modifying the prepared source tree:

```sh
audio_build_dir=$(mktemp -d /tmp/uconsole-usb-audio.XXXXXX)
cp -a /path/to/pinned/kernel-source/sound/usb/. "$audio_build_dir/"
patch -d "$audio_build_dir" -p3 < Code/patch/cm4/20260414/0003-usb-audio-drain-period.patch
make -C /path/to/prepared/kernel-output ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  M="$audio_build_dir" -j2 modules
sha256sum "$audio_build_dir/snd-usb-audio.ko"
```

Pass that module and its explicit hash to the disposable guest validator:

```sh
python3 tools/validate_audio_guest.py --image /path/to/base.img \
  --sha256 BASE_SHA256 --output /path/to/new-validation-directory --wav --usbmon \
  --module /path/to/candidate/snd-usb-audio.ko --module-sha256 MODULE_SHA256
```

The validator freezes the pinned bytes, uploads them only to guest `/tmp`,
unloads the stock USB audio module and loads the candidate normally. It neither
force-loads modules nor replaces installed module files. The first candidate
passed on the official `6.12.62-v8+` guest: all 48,000 tone samples matched after
constant mixer gain, and USB monitoring accounted for all 192,000 bytes. Evidence
and candidate bytes are retained in
`build/emulator/audio-drain-module-20260925/`. Stock-driver failures remain
recorded for comparison. A follow-up using `--repetitions 3` passed all three
independent open/play/drain/close cycles: 144,000 matched frames and 192,000
submitted/completed USB bytes per stream, with clean shutdown and unchanged
base. Its evidence is in `build/emulator/audio-drain-repeat-20260925/`.
Broader ALSA regression and physical USB-audio qualification remain open;
this patch is not automatically installed
into user images or the physical uConsole.

### Candidate late-poweroff fix

`0002-bcm2835-i2c-atomic-transfer.patch` targets the same pinned source revision.
It adds bounded polling for atomic I2C transfers while retaining interrupt-driven
ordinary transfers. The AXP power-off handler uses I2C after interrupts are
disabled; the original controller driver has no atomic callback. Apply this
candidate after the panel cleanup patch when testing the fix:

```sh
patch -p1 < /path/to/uConsole/Code/patch/cm4/20260414/0002-bcm2835-i2c-atomic-transfer.patch
```

The module builds against the pinned CM4 output tree and loads into the official
6.12.62-v8+ guest without force-loading or bypassing symbol CRC checks. A
maintenance guest passes ordinary AC reads followed by read-only remount and
late kernel power-off without the prior atomic-I2C/RCU shutdown warnings.
This is not yet full desktop shutdown, atomic error-path or physical-bus
qualification. The patch is not automatically installed into user images.
It changes a real controller driver, not an emulator-only guest interface, so
the eventual image can use the same driver on hardware after qualification.

The retained test module is `build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko`.
Repeat its kernel-path acceptance on a disposable prepared workspace:

```sh
python3 tools/validate_kernel_poweroff.py --workspace /path/to/disposable-workspace \
  --module build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko
```

The validator loads the candidate from guest `/tmp`, without replacing installed
modules. It explicitly syncs/remounts root read-only, then calls forced kernel
power-off from the maintenance shell; no normal userspace shutdown is claimed.
Failed validation may force-stop only its owned QEMU process.

`tools/kernel-tests/uconsole_i2c_atomic_test.c` is a disposable-emulator test
module, not a driver to install on devices. It invokes the adapter's atomic
callback with preemption and interrupts disabled, testing a repeated-start
AXP identity read, an absent-address NACK, atomic recovery and ordinary IRQ-driven
recovery. It requires explicit `confirm_disposable=1`; the validator supplies
that flag and the emulator's direct adapter number. Build it against the same
prepared kernel output (example paths from the repository root):

```sh
mkdir -p build/kernel-cm4/atomic-i2c/tests
cp tools/kernel-tests/Makefile tools/kernel-tests/uconsole_i2c_atomic_test.c \
  build/kernel-cm4/atomic-i2c/tests/
make -C /path/to/prepared/kernel-output ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  M="$PWD/build/kernel-cm4/atomic-i2c/tests" modules
python3 tools/validate_kernel_poweroff.py --workspace /path/to/disposable-workspace \
  --module build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko \
  --test-module build/kernel-cm4/atomic-i2c/tests/uconsole_i2c_atomic_test.ko
```

The extended test passes on the official guest with the candidate driver,
including subsequent late power-off. It does not yet exercise a stalled-bus
timeout, long transfers, competing IRQ load or physical repeated-start timing.

For the separate normal-systemd shutdown test, use:

```sh
python3 tools/validate_systemd_poweroff.py --workspace /path/to/disposable-workspace \
  --module build/kernel-cm4/atomic-i2c/i2c-bcm2835.ko
```

This validator boots maintenance mode to stage uniquely named test files, then
boots the unchanged guest kernel in normal multi-user mode. A temporary timer
loads the candidate through ordinary module loading and requests `systemctl
poweroff --no-block` only after multi-user startup. Both timer and service require
a unique kernel-command-line token supplied by the validator, so the fixture
does not activate on native boot. A final maintenance boot removes the exact
per-run files and stops cleanly. A failed run may force-stop its owned guest;
inspect its JSON evidence and cleanup status before reusing the workspace.
Normal boot and shutdown have a combined 600-second deadline, configurable with
`--boot-shutdown-timeout`; first-boot resizing may reboot within this window.
A separately marker-gated, read-only diagnostic timer prints pending systemd
jobs, failed units and the last 80 journal entries after 120 guest seconds.
It does not wait for multi-user startup or disable services, and its unique
fixture files are removed with the shutdown-test files.
After successful shutdown, before cleanup boots the guest again, the validator
checks the stopped overlay's primary ext4 superblock for clean-state flags and
checksum validity. This is not a full filesystem consistency check.
This is not desktop-session or hardware qualification.

The normal multi-user test passes with the candidate module: ordinary rebind,
AC read, systemd power-off, zero QEMU exit, clean primary ext4 superblock and
fixture removal. See the retained result in [validation](../../../../docs/emulator-validation.md).

For hot replacement, the validator unbinds PMIC device `22-0034` before unloading
the stock I2C adapter, then verifies it rebinds after inserting the candidate.
The official image builds the PMIC driver into the kernel, so module removal
is not available. The pinned stock
adapter frees its IRQ before deleting child devices; directly unloading it
with an active PMIC can strand the child's register-writing teardown. This
test ordering keeps teardown on a live bus and does not remove the PMIC from
the shutdown path being qualified.

### Kernel profiles

Select one profile and a separate output directory for each:

| Core | Defconfig | Output directory | Boot image name |
| --- | --- | --- | --- |
| CM4 | `bcm2711_defconfig` | `build-cm4` | `kernel8.img` |
| CM5 | `bcm2712_defconfig` | `build-cm5` | `kernel_2712.img` |

The CM5 configuration uses 16 KiB pages. Keep each image, configuration and
module directory together. These builds have been verified on an ARM64 Linux
host; boot and peripheral operation still require the target hardware.

From the kernel source directory (CM4 example; use the table for CM5):

```sh
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
KERNEL_BUILD="$PWD/build-cm4"
BOOT_IMAGE=kernel8.img
DEFCONFIG=bcm2711_defconfig

mkdir -p "$KERNEL_BUILD/tmp"
export TMPDIR="$KERNEL_BUILD/tmp"
make O="$KERNEL_BUILD" "$DEFCONFIG"
# Optional customization: make O="$KERNEL_BUILD" menuconfig
make O="$KERNEL_BUILD" -j8 Image.gz headers modules dtbs
```

`TMPDIR` keeps compiler scratch files on the build filesystem, including when
that filesystem is RAM-backed or on a separate disk. Source and object files
alone being on another filesystem does not stop GCC from filling `/tmp`.

## Stage a kernel archive

Continue in the same shell after a successful build. This stages files without
installing anything into the host's `/boot` or `/lib/modules`:

```sh
VERSION=$(make -s O="$KERNEL_BUILD" kernelrelease)
KERNEL_STAGE="$PWD/install-$VERSION"
mkdir -p "$KERNEL_STAGE/boot/firmware/overlays"
make O="$KERNEL_BUILD" INSTALL_MOD_PATH="$KERNEL_STAGE" modules_install

cp "$KERNEL_BUILD/arch/$ARCH/boot/Image.gz" "$KERNEL_STAGE/boot/firmware/$BOOT_IMAGE"
cp "$KERNEL_BUILD/arch/$ARCH/boot/dts/broadcom/"*.dtb "$KERNEL_STAGE/boot/firmware/"
cp "$KERNEL_BUILD/arch/$ARCH/boot/dts/overlays/"*.dtbo "$KERNEL_STAGE/boot/firmware/overlays/"
cp "arch/$ARCH/boot/dts/overlays/README" "$KERNEL_STAGE/boot/firmware/overlays/"
cp "$KERNEL_BUILD/.config" "$KERNEL_STAGE/boot/config-$VERSION"
cp "$KERNEL_BUILD/System.map" "$KERNEL_STAGE/boot/System.map-$VERSION"

# The build symlink points to this machine's source/output and is not portable.
tar --exclude="./lib/modules/$VERSION/build" \
    -czf "install-$VERSION.tar.gz" -C "$KERNEL_STAGE" .
```

This produces a tar archive, not a Debian package. `make headers_install`
exports userspace API headers; those alone are not a complete kernel header
package for building external modules. Use the kernel's supported packaging
workflow if a Debian image/header package is needed.

## Check compiled display overlays

Use Raspberry Pi's [dtmerge utility](https://github.com/raspberrypi/utils/tree/master/dtmerge)
for overlays with Raspberry Pi parameters and dormant fragments. Generic
`fdtoverlay` can reject the combined display overlays even though `dtmerge`
applies them successfully.

From the uConsole repository, after building the kernel and `dtmerge`:

```sh
python3 tools/verify_display_overlays.py \
  --dtmerge /path/to/dtmerge \
  --dts-dir /path/to/kernel/build-cm4/arch/arm64/boot/dts \
  --output build/kernel-cm4 --model cm4
```

For CM5, select its output directory and `--model cm5`. The check combines
the uConsole and VC4 overlays with `cma-384`, then checks panel orientation,
enabled display nodes, battery property and CMA size. It emits the merged DTB
and JSON evidence. This is structural validation, not a hardware boot test.

# config.txt

config.txt needs some modifications for cm4/cm5 to boot.
```
[pi4]         
#dtoverlay=clockworkpi-devterm      
dtoverlay=clockworkpi-uconsole   
dtoverlay=vc4-kms-v3d-pi4,cma-384  
enable_uart=1

[pi5]
#dtoverlay=clockworkpi-devterm-cm5
dtoverlay=clockworkpi-uconsole-cm5
dtoverlay=vc4-kms-v3d-pi5,cma-384
dtparam=uart0
dtparam=pciex1
dtparam=pciex1_gen=3

[all]                                                                                                                                                   
ignore_lcd=1 
max_framebuffers=2
disable_overscan=1
dtparam=audio=on
dtoverlay=audremap,pins_12_13
dtoverlay=dwc2,dr_mode=host
dtparam=ant2
dtparam=spi=on
dtoverlay=spi0-0cs
gpio=10=ip,np
gpio=9=op,dh
```
