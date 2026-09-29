# Upstream issue worklist

Snapshot: 2026-09-20, upstream `clockworkpi/uConsole` at `53a05efca806ca024aab4fbfd28a680688c2541c`.
All 28 open issues were retrieved with their comments. This worklist preserves the full scope; a local fix or a documentation response is not proof that an upstream issue is resolved. No issue has been closed or external comment posted.

## External dependencies for completion

The final refresh still lists the same 28 open issues. Local builds, tests,
package checks and read-only inspection of all four published images have
provided the evidence below. Completion still requires:

- Access to the affected uConsole models for boot, display/input, modem,
  power, suspend/shutdown, expansion-bus and desktop acceptance.
- A provenance-checked replacement for the unavailable A06 Wi-Fi source,
  and the documented Allwinner SDK/toolchain for the R01 kernel build.
- Manufacturer-owned editable CAD, BOM, mechanical models, licensing
  decisions, new-board design and stock information.
- Vendor package-repository access/review to publish Quickshell and image
  updates. The local package is not a vendor release.

All image mounts and inspection loop devices have been released, and the
temporary build containers stopped. Build artifacts and evidence are under
`build/`; temporary source caches and downloaded archives remain on the RAM
filesystem and will not survive a reboot. Changes are organized into focused
local commits; generated build artifacts are not tracked by Git.

## Build evidence

`make all check` passes on this ARM64 host. Firmware: 33,696 bytes flash and 4,680 bytes RAM. The native serial reset helper builds with `-Wall -Wextra -Werror`. All 30 tests pass under system Python and Xvfb, including mocked DFU failures, serial re-enumeration timeout, reset failure while already in DFU mode, argument handling, and the CM5 power sequences. ShellCheck passes for the modified shell scripts. Firmware and modem behavior have not been tested on uConsole hardware. CM4 Linux 6.12.62-v8 built successfully (Image.gz, headers, all modules and DTBs); all 1,911 modules installed into staging with depmod metadata, and the panel module reports matching vermagic. `build/kernel-cm4/install.tar.gz` contains the image, DTBs, modules, configuration and System.map. Combined CM4 and CM5 uConsole/VC4 overlays apply with Raspberry Pi dtmerge; panel orientation, display enablement, CMA and battery properties are verified by tools/verify_display_overlays.py. Evidence, configuration and image are saved under `build/kernel-cm4/`. OS images and A06/R01 kernels remain unbuilt. The pinned CM4 source/build resides in a 32 GiB temporary RAM filesystem at `/tmp/uconsole-kernel-work`; outputs are temporary until copied into the repository build directory. GCC temporaries are also placed there after disk-backed /tmp filled during the first attempt. CM5 bcm2712_defconfig also passed as 6.12.62-v8-16k, with 1,910 staged modules; its configuration, logs, image, archive and checksums are in build/kernel-cm5/. The five unused panel-driver locals reported during compilation have a local patch; the resumed complete build passed without warnings.

`make flash-bundle` produces `build/uconsole_keyboard_flash-aarch64.tar.gz` with the current firmware and upload tools. Archive contents were byte-compared with build outputs; the extracted ARM64 helper executes and correctly rejects a missing serial device. No keyboard was flashed.

Installed toolchain: `~/.local/bin/arduino-cli`, `~/Arduino/hardware/stm32duino/STM32F1`, and `~/.local/share/uconsole-toolchain/compiler`. See the keyboard README for settings and archive checksums.

## Remaining work by issue

| Issue | Status / next evidence needed |
| --- | --- |
| [#47 ADD Bom and cpl file](https://github.com/clockworkpi/uConsole/issues/47) | Found 223 unique placement records in the mainboard SMT export; documented extraction in PCB/README.md. Purchasing BOM and manufacturer-qualified CPL still require owner data/verification. |
| [#46 Bug: Scandinavian keyboard needs to be included](https://github.com/clockworkpi/uConsole/issues/46) | Verified Nordic host mappings with libxkbcommon (se/fi/dk/no) and documented them in wiki/Troubleshooting.md. Physical Nordic keycaps/ISO hardware remain a manufacturer feature request. |
| [#45 request feature add on](https://github.com/clockworkpi/uConsole/issues/45) | Quickshell v0.3.1 builds with default features in Trixie ARM64 and all nine test suites pass with a test-only asynchronous-position fix. Build instructions and patch are in Code/patch/quickshell/. A local ARM64 Debian package is saved in build/quickshell/; clean Trixie installation and an unprivileged QtQuick window smoke test pass. tools/package_quickshell.py records the packaging process and exact Qt private ABI dependencies. Physical Wayland acceptance and vendor repository publication remain open; publication requires the repository owner. |
| [#44 The uconsole-4g-cm5 script seems overengineered and for some use cases too specific](https://github.com/clockworkpi/uConsole/issues/44) | Implemented locally; six mocked tests and ShellCheck pass. CM5 electrical timing and modem detection still require hardware validation. |
| [#41 260 pin mainboard variant](https://github.com/clockworkpi/uConsole/issues/41) | Requires a new mainboard design and electrical/mechanical validation. |
| [#40 will there ever be an u console re stock](https://github.com/clockworkpi/uConsole/issues/40) | Manufacturing/restock information must come from ClockworkPi. |
| [#39 Why can't open-source PCBs be recognized?](https://github.com/clockworkpi/uConsole/issues/39) | Both archives pass integrity tests. Gerber RS-274X exports and available placement/drill files are documented in PCB/README.md; native EasyEDA/KiCad sources and complete fabrication qualification remain unavailable. |
| [#38 Can I bring back instant shutdown with power button..](https://github.com/clockworkpi/uConsole/issues/38) | Documented Xfce power-manager/logind ownership and diagnostics in wiki/Troubleshooting.md, based on Xfce documentation. Affected desktop/image and physical button behavior still need verification. |
| [#35 4G module](https://github.com/clockworkpi/uConsole/issues/35) | Found both bundled flash scripts continue after errors. Added checksum/device preflight and stop-on-failure updater, 7 tests, and offline validation of both real packages. Native fastboot rebuild passes with path-bounds patch. Added Tk GUI, desktop menu entry, staged installation, six Xvfb GUI tests and two CLI discovery/installed-helper tests. Visually checked at 720 pixels wide. Physical upgrade and desktop Polkit authorization validation remain open. |
| [#34 Can you keep the OS images a bit more up to date ?](https://github.com/clockworkpi/uConsole/issues/34) | CM4 April 2026 image appears in README; A06 U-Boot package built in isolated Jammy ARM64. The Linux 5.15.119 attempt fails because the pinned Armbian script fetches the unavailable 150balbes/wifi local_rtl8822bs branch, then adds a Kconfig reference to the missing driver. Logs and U-Boot package are saved in build/kernel-a06/. A provenance-checked driver source or an updated build integration is still needed; no drivers were disabled to bypass this failure. R01 requires its documented Allwinner SDK; vendor access requires account/key enrollment. Full image build verification remains open. |
| [#32 KiCAD files 4G modem](https://github.com/clockworkpi/uConsole/issues/32) | Requires editable modem PCB design files from hardware owner. |
| [#31 Can't find battery board files to customize it.](https://github.com/clockworkpi/uConsole/issues/31) | Requires battery board schematic and editable design files from hardware owner. |
| [#29 CM5 support?](https://github.com/clockworkpi/uConsole/issues/29) | Pinned kernel includes CM5 overlay and bcm2712_defconfig (16 KiB pages); the separate full CM5 build and module staging passed, as did combined display-overlay validation. Read-only inspection of the verified CM4 v3.1 image confirms CM5 kernel/modules and model-specific boot configuration; its actual shipped CM4/CM5 overlays also pass validation. Target boot/peripherals remain unverified. |
| [#27 Open-Source Hardware Compliance ](https://github.com/clockworkpi/uConsole/issues/27) | Inventory complete in PCB/README.md: no repository-wide hardware license or editable CAD project found. Owner must supply missing sources/license; manufacturing exports cannot recover them faithfully. |
| [#22 Can you provide the PCBs for it?](https://github.com/clockworkpi/uConsole/issues/22) | Gerber exports inventoried in PCB/README.md; archives pass integrity checks. Editable design sources and complete manufacturing qualification remain missing. |
| [#20 SPI & i2c Disabled?](https://github.com/clockworkpi/uConsole/issues/20) | Pinned CM4 overlay enables I2C0/I2C1 and SPI4; pin ownership and diagnostics documented in wiki/Troubleshooting.md. Actual expansion wiring/image and peripheral operation still required. |
| [#14 Where are PCBs?!](https://github.com/clockworkpi/uConsole/issues/14) | Gerber exports inventoried in PCB/README.md; archives pass integrity checks. Editable design sources and complete manufacturing qualification remain missing. |
| [#13 Running kodi screen display direction is incorrect](https://github.com/clockworkpi/uConsole/issues/13) | Current panel driver publishes 720x1280 mode and orientation; CM4 overlay rotation is 90 degrees. Backend-specific diagnostic guidance added; old-image Kodi reproduction remains open. |
| [#12 OS image links](https://github.com/clockworkpi/uConsole/issues/12) | All four Google Drive HTTPS pages return HTTP 200 and are now primary links; the community mirror also works over HTTPS. Manufacturer port 443 fails to connect here. All four complete HTTPS archive downloads now match their published MD5 values; independently calculated SHA-256 values and byte counts are in docs/os-image-verification.md and docs/os-images.sha256. No hardware boot is implied. |
| [#11 No power / battery settings for CM4](https://github.com/clockworkpi/uConsole/issues/11) | Added read-only kernel and desktop capability checks; actual CM4 suspend/resume and battery UI require target verification. |
| [#10 dmesg: axp20x-battery-power-supply: couldn't set constant charge current from DT](https://github.com/clockworkpi/uConsole/issues/10) | CM4 patch already contains constant-charge-current-max-microamp, fixed upstream in 0e9fc3f. R01 patches use the same spelling. Pinned CM4/CM5 6.12.62 overlays also have the corrected property; actual charging behavior remains unverified. |
| [#9 Please add instructions on how to compile the keyboard firmware](https://github.com/clockworkpi/uConsole/issues/9) | Firmware compiles on ARM64 after min/abs compatibility fixes; Makefile and CLI instructions added. DFU failure reporting and native reset errors are fixed with tests; physical flashing remains open. |
| [#7 hardware sources?](https://github.com/clockworkpi/uConsole/issues/7) | PCB/README.md inventories manufacturing exports; editable CAD projects remain unavailable and must come from the owner. |
| [#6 Still no 3D models.](https://github.com/clockworkpi/uConsole/issues/6) | No STEP/STL or native mechanical models found in the checkout/PCB archives; owner must provide design data or drawings sufficient for verified modeling. |
| [#5 devterm-fan-temp-daemon running and generating errors](https://github.com/clockworkpi/uConsole/issues/5) | Documented disabling the named DevTerm-only daemon on affected uConsole images. Verified the actual CM4 v3.1 image: no devterm-fan-temp-daemon package or service was found in the inspected package/service inventory. The available CM4 v0.1b archive has only a dangling enablement symlink, with no fan package/service/script; documented exact cleanup. A06 has a different enabled daemon that also changes CPU/GPU policies, documented separately. Live service behavior still needs target verification. |
| [#3 R-01 shutdown time very long, power button doesn't shut it off.](https://github.com/clockworkpi/uConsole/issues/3) | Added stop-job diagnostics. The verified v1.3b image includes historical SIGTERM timeout logs for multiple services; this supports the reported symptom but does not isolate a cause. Reproduction and fix still require R01 hardware/current shutdown logs. |
| [#2 R-01 cursor missing in multi-user mode.](https://github.com/clockworkpi/uConsole/issues/2) | Documented Linux software-cursor sequence and guarded login configuration; Read-only v1.3b inspection confirms framebuffer console/rotation support and no explicit cursor override in the inspected boot files. Cursor rendering remains unverified on hardware. |
| [#1 Change system name on network setup doesn't work. R-01](https://github.com/clockworkpi/uConsole/issues/1) | Current CM4 pi-gen writes both hostname and hosts at image creation; the actual R01 v1.3b image also starts with matching entries. R01 desktop rename integration remains unverified. Added focused diagnostics and hosts-entry repair guidance. |
