# uConsole

## uConsole Workbench IDE

The uConsole Workbench is the primary packaged application in this repository:
a desktop source editor, CM4 emulator console, image/file-transfer tools, and
agent task interface. The release package also includes the keyboard firmware
flasher built for the host operating system and CPU architecture.

The same Make targets work on supported macOS and Debian/Ubuntu Linux hosts
(`x86_64` and `aarch64` on Linux; Apple silicon and Intel on macOS):

```sh
make deps       # install build and runtime dependencies
make build      # build the native IDE and its cached, pinned patched QEMU
make run        # launch the IDE from this checkout
make package    # create build/uconsole-workbench-OS-ARCH.tar.gz
sudo make install
```

Workbench and MCP require Python 3.12+; Workbench also requires Tk. The installed
launchers skip older interpreters, including macOS's bundled Python 3.9. Set
`PYTHON` to a compatible interpreter if it is not found automatically.

`make deps` uses Homebrew on macOS and `apt-get` on Linux. Installing the
legacy STM32F1 Arduino core can require the manual architecture-specific setup
in [the keyboard firmware guide](Code/uconsole_keyboard/README.md). Override
`PREFIX` and `DESTDIR` for staged or non-default installations. Installed IDE
state and emulator images live below
`${XDG_DATA_HOME:-$HOME/.local/share}/uconsole-workbench`, outside the install
tree. The Workbench opens the installed keyboard firmware source at startup,
and the matching firmware and flashing tools are installed under
`$PREFIX/share/uconsole-keyboard-flash`.

Native packages include the pinned, patched QEMU and its runtime data and
corresponding source archive. The startup progress window checks its
patch fingerprint and offers a cancellable source rebuild with persistent logs.
A compatible user build takes precedence over the bundled copy; a generic system
QEMU is not accepted by the IDE's Start action. Source builds cache QEMU until
its build inputs change. Packages still require platform runtime shared libraries.
Press **Start** for the standard environment: prerequisite checks, any needed
emulator build, a verified default Linux image download, desktop preparation,
and boot run as one workflow. Approve the 2.21 GB official CM4 v3.1 HTTPS download
when prompted; no image search or checksum entry is needed. Downloads are cached,
resumable and SHA-256 verified before import. Allow at least 20 GiB free.
Missing Debian/Ubuntu packages can be installed after approval through the system
authentication prompt; on macOS with Homebrew, installs run as your user.
Workbench never runs itself as root. Cancelling startup allows an active package
transaction to finish safely, then stops subsequent steps. Custom image import
remains under Advanced tools. Existing workspaces are never overwritten.
No second Start is required after preparation. The progress window
keeps its log and remains open until dismissed without stopping the guest.
New sessions default to Desktop with a native graphical display. Maintenance mode starts a root
shell, not a desktop; press Enter in the serial input if kernel messages bury
the prompt. Select Desktop and a graphical display backend for the desktop.

**Setup** opens a configuration wizard: choose image and hardware settings,
review download/package permissions, then **Finish** runs setup through startup.
**Cancel** discards wizard edits. System authentication may still be required.

**Live Schematic** on the toolbar opens a clickable hardware map with source
navigation, offline schematic sheets, observed emulator activity and recording/
replay. See [the schematic walkthrough](docs/workbench-demo-ideas.md) for its
instrumentation and fidelity boundaries. Activity counters require the current
patched QEMU build. Off, disabled, disconnected, waiting and unavailable states
are distinguished; physical SSH targets do not supply schematic telemetry.

Inside the application, **Help → User guide** (F1) opens the searchable offline
guide. Hover over controls for hints, or focus a control and press F1 for its
topic. **Image → Import guest image** prepares a supported CM4 Linux image;
the included keyboard firmware is not a guest operating system.
**Help → Diagnostics** displays the application log and copyable error details.
Errors are also emitted to stderr as JSON lines and persisted privately at
`${XDG_STATE_HOME:-$HOME/.local/state}/uconsole-workbench/application.jsonl`.
Review diagnostic text before sharing it: error messages may contain local paths
or external-command details. Guest transcripts and job evidence remain separate.

Releases are built by GitHub Actions for Linux x86_64, Linux AArch64, and macOS
Apple silicon. Each archive contains the IDE, its supporting tools and docs,
and a native keyboard-flashing bundle. `SHA256SUMS` is published alongside the
archives. Maintainers can validate without tagging or create a release with:

```sh
./scripts/release.sh --dry-run 1.0.0
make release RELEASE=1.0.0
```

## CM4 emulator and development workbench

The [emulator guide](docs/emulator.md) provides a local QEMU build, official-image
boot, serial console, writable image overlays, file transfer and image export,
plus a Python/Tk source editor and emulator workbench. This is **partial CM4
emulation**; the guide records missing uConsole devices and platform validation.
The [full-device plan](docs/emulator-device-plan.md) maps the schematics,
firmware and Linux drivers to staged models and acceptance gates.

Live hardware development treats the uConsole as an SSH target: edit/build on
the host, test in the IDE, retain a verified host-side backup of affected target
state, then deploy and validate with an explicit restore action available.
No spare SD card or reader is required. External-card flashing is a separate,
optional deployment path, not the live-development loop. The
[physical-target workflow](docs/forge-agents.md) supports reviewed file and
standalone-service transactions; whole-system and boot recovery remain separate
qualification requirements. A successful deployment need not be immediately
undone, but its backup and restore path must be retained.

## Building source

Run `make deps`, or install the
[keyboard firmware toolchain](Code/uconsole_keyboard/README.md) and a native C
compiler, Make, Python 3, `patch`, `tar`, and ShellCheck. Then:

```sh
make all check
make flash-bundle
```

This builds the keyboard firmware, serial reset helper and modem fastboot
tool, runs the automated checks, and packages a keyboard flashing bundle for
the helper's architecture under `build/`. It does not flash hardware or
install a kernel on the build host. See the [flashing guide](Bin/uconsole_keyboard_flash/README.md)
and [modem updater guide](Code/scripts/README.md) for device operations.

Kernel/OS builds use separate source trees and toolchains: [CM4/CM5](Code/patch/cm4/20260414/README.md),
[A06](Code/patch/a06/20230630/README.md), and [R01](Code/patch/r01/20230614/README.md).
The [upstream issue worklist](docs/upstream-issues.md) records build evidence
and outstanding hardware/manufacturer dependencies.
The [Quickshell build and package guide](Code/patch/quickshell/README.md)
covers the optional Trixie desktop toolkit requested in issue #45.


## uConsole OS Images

|Product | Image name  |Release date | System  | Kernel |Size|      Downloads      | MD5sum | 
|--------|:----------|:------------|:--------|:--------------|:------------|:--------------|:---------:| 
|uConsole CM4 | uConsole_CM4_v3.1_64bit.img.bz2| April  14 2026 | 64-bit| Linux 6.12.62| 2.1G | [Download (HTTPS)](https://drive.google.com/file/d/17OFCPCBwddaqQ957R4vbY-3KzugGtaU-/view?usp=drive_link) ([Manufacturer mirror (HTTP)](http://dl.clockworkpi.com/uConsole_CM4_v3.1_64bit.img.bz2)) | 1b31b501d11d95da7f1380d50d47a8a9 |
|uConsole CM4 | uConsole_CM4_v0.1b_xfce_64bit.img.7z| Apr  5  2023 | 64-bit| Linux 5.10.17| 1.2G | [Download (HTTPS)](https://drive.google.com/file/d/1gBpVK1rMM5zmq5z4-DlKlDmTFJQvwMFz/view?usp=drive_link) ([Manufacturer mirror (HTTP)](http://dl.clockworkpi.com/uConsole_CM4_v0.1b_xfce_64bit.img.7z)) | a191603d7da0f826d347f1bb8d525687 |
|uConsole A06 | uConsole_A06_v1.1e.img.7z| Jul  1  2023 | 64-bit| Linux 5.15.119| 1.4G | [Download (HTTPS)](https://drive.google.com/file/d/1H5wdB_cCSW-qbf6Ijgcn-Uw4EQQkWrP1/view?usp=drive_link) ([Manufacturer mirror (HTTP)](http://dl.clockworkpi.com/uConsole_A06_v1.1e.img.7z)) | 56bbb623f41bf6327d408fb415052819 |
|uConsole R01 | uConsole_R01_v1.3b.img.7z| Jun 13  2023 | 64-bit| Linux 5.4.61| 1.4G | [Download (HTTPS)](https://drive.google.com/file/d/1lDdO3-aj8zeO6JDJVBaCgNkehhffdNm1/view?usp=drive_link) ([Manufacturer mirror (HTTP)](http://dl.clockworkpi.com/uConsole_R01_v1.3b.img.7z)) | 53ca37ccc0333436d06fb5978ac699fd |


uConsole_CM4_v0.1b_xfce_64bit.img.7z  (based on [RPI-lite](https://downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2023-05-03/2023-05-03-raspios-bullseye-armhf-lite.img.xz) with xfce)   
 This version is optimized for immersive writing and comes pre-installed with [Obsidian](https://obsidian.md/).



## Images mirror

All four HTTPS archive payloads were downloaded and checked on 2026-09-20;
their MD5 values match the table above. See the [verification record and SHA-256 checksums](docs/os-image-verification.md).

* Community-driven download mirror: [https://dl.clockworkpi.io](https://dl.clockworkpi.io)


After downloading the files, you will need to extract or decompress them. Please keep in mind that MacOS 11.6 or a higher version is required to extract 7z files.  
  
To flash the OS image, you can use the following tools:  
  
- For Windows and macOS users, [Etcher](https://etcher.balena.io/) can be used to flash the image.  
- Linux users can employ the "dd" command to flash the image.  

To learn how to create an image, please refer to our [Wiki](https://github.com/clockworkpi/uConsole/wiki).  

If you want to use the 4G extension, you can find helpful tips on how to use it on the [uConsole Wiki](https://github.com/clockworkpi/uConsole/wiki/How-to-use-the-4G-extension).

## uConsole Keyboard Firmware
Build the current firmware and upload tools with `make flash-bundle`; the
result is `build/uconsole_keyboard_flash-OS-ARCH.tar.gz`. Use a bundle built
for the machine that will perform the upload. Release users normally receive
this flasher inside the larger Workbench archive. The
[flashing guide](Bin/uconsole_keyboard_flash/README.md) describes requirements
and error handling.

Here's how you can flash the firmware on uConsole(A06 or CM4) or a PC running Ubuntu 22.04:

1. Build or obtain the current flashing bundle for the upload machine.
2. Extract the archive, substituting its target: `tar xzf uconsole_keyboard_flash-OS-ARCH.tar.gz`.
3. Install the required package using the following command: `sudo apt install -y dfu-util`.
4. Navigate to the extracted directory: `cd uconsole_keyboard_flash`.
5. Execute the flash script with root privileges: `sudo ./flash.sh`.
6. If everything goes well, you will see a progress bar indicating the flashing process.
7. If the command fails, retain its output and follow the flashing guide before retrying. A failed upload is not reported as success.

## 4G extension firmware

[Upgrade 4G extension firmware](https://github.com/clockworkpi/uConsole/wiki/How-to-upgrade-4G-extension-firmware)  


## Assembly Guidelines

* [Assembly Guidelines](https://github.com/clockworkpi/uConsole/blob/master/Clockwork_uConsole_Assembly_Guidelines.pdf)  

## Hardware design files

See the [hardware file inventory](PCB/README.md) for PCB manufacturing exports,
component placement data, supported file formats, and missing editable sources.

## Schematic

* [A06 core mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_DevTerm_A06_Core_for_Mainboard_V3.14_Schematic.pdf)
* [R01 core mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_DevTerm_R01_Core_for_Mainboard_V3.14_Schematic.pdf)
* [Mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Mainboard_V3.14_Schematic.pdf)  
* [Mainboard v3.14-V5 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Mainboard_V3.14_V5_Schematic.pdf)  
* [CM4 adapter schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Adapter_CM4_Schematic.pdf)
* [4G expansion Schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_UC_4G_Schematic.pdf)


## Gearbox 

### A06 
Gearbox is a script tool used to adjust the big.LITTLE architecture of the A06 chip.  
you can get it by running 
```
sudo apt update 
sudo apt install -y devterm-gearbox-a06
```

In latest os image of A06, default Gearbox is set to use 4 LITTLE core with 816Mhz,GPU at 400Mhz

you can run `a06-gearbox` to see the current core status.

```
Current Status:
+-----------------------------------+-----------------+-----------+
|            Cortex-A53             |   Cortex-A72    | Mali-T860 |
+--------+--------+--------+--------+--------+--------+-----------+
| CPU 0  | CPU 1  | CPU 2  | CPU 3  | CPU 4  | CPU 5  |    GPU    |
+--------+--------+--------+--------+--------+--------+-----------+
| 816Mhz | 816Mhz | 816Mhz | 816Mhz |  OFF   |  OFF   |   400MHz  |
+--------+--------+--------+--------+--------+--------+-----------+
CPU Governor: schedutil    GPU Governor: simple_ondemand
```

Run `sudo a06-gearbox -s [GEAR]` to set gear,GEAR would be 1,2,3,4,5,6

There are 6 gears in gearbox

```
               1 for simple writing tasks with long battery life.
               2 for browsing most websites with long battery life.
               3 for most 2D games and emulators.
               4 for playing videos and 3D games.
               5 for performance-first tasks.
               6 for max performance, max power (usage).

```



## Community
Please visit our [Github Wiki](https://github.com/clockworkpi/uConsole/wiki) and https://forum.clockworkpi.com for more information.
