# CM4 emulator and development workbench

This repository now has a **partial CM4 development environment**, based on
QEMU's `raspi4b` BCM2711 model. It boots the official CM4 kernel and filesystem,
provides a maintenance shell, an AXP221 PMIC, USB input substitutes, writable
disk overlays, image export, QMP controls, and a desktop source editor/serial
workbench with a machine-readable agent interface.
It is **not yet a complete virtual uConsole**. In particular, it does not validate
the DSI display, VideoCore GPU, battery/charging behavior, STM32 keyboard
firmware, or the complete modem.

The staged path from functional surrogates to driver- and hardware-level
coverage for every carrier device is tracked in the
[full-device emulation plan](emulator-device-plan.md).

## Research and adoption decision

Research performed 2026-09-21:

* [QEMU Raspberry Pi models](https://www.qemu.org/docs/master/system/arm/raspi.html)
  include a Pi 4B with four Cortex-A72 CPUs and 2 GiB of RAM. QEMU supplies the
  useful CPU, interrupt, storage, UART, USB and mailbox foundation. Its documented
  missing devices include PCIe, GENET Ethernet and PWM. The upstream development
  documentation is newer than the pinned build; source inspection and local
  tests establish the capabilities actually used here.
* [QEMU 10.2.4 source](https://github.com/qemu/qemu/tree/v10.2.4) is the pinned
  baseline. The host's preinstalled QEMU 8.2.2 does not have `raspi4b`.
* [void-uconsole](https://github.com/Ravenhammer-Research/void-uconsole) documents
  a Pi 3 QEMU launch for its image. It is an archived OS project, not a complete
  uConsole device model. Reuse upstream QEMU rather than adopting this wrapper.
* [Renode](https://github.com/renode/renode) is relevant to future MCU co-simulation,
  but this search did not identify a ready-made CM4/uConsole platform to adopt.
* A [ClockworkPi community request](https://forum.clockworkpi.com/t/emulate-the-cm4/15122)
  asks for the same capability; the thread does not supply a working full-device
  emulator. This is evidence of a gap, not proof that no other project exists.

Generic QEMU `virt` machines can eventually provide a faster application-testing
profile, but they do not exercise BCM2711 hardware or boot the CM4 kernel in the
same way. They are not presented here as CM4 emulation.

## Connections and fidelity

`make emulator-build` builds the pinned QEMU without replacing the system
installation. Source and build caches are separated by patch contents and build
recipe under `build/emulator/qemu-cache/`. Changing a patch starts a new source
tree; it does not attempt to undo old patches in place. The stable `qemu-build`
path is switched only after both binaries build and pass their version checks.
An existing directory at that path is retained as `qemu-build.legacy-*` during
the first migration. Failed builds leave the previous public build selected.
Old generations are retained, not automatically deleted; allow space for an
additional source/build tree when upgrading. Builds sharing this cache are
serialized. This is a build cache, not a hermetic toolchain: compiler or SDK
upgrades may still require a fresh cache. The selected build's `qemu-source`
link identifies its matching source for source-level validation.

Serial is one development connection, not the whole device interface.
The repository's keyboard firmware initializes USB composite HID keyboard,
mouse/trackball, joystick, consumer controls, and a USB serial interface.
Firmware flashing uses USB reset/DFU. The core also has hardware UARTs. The
optional modem uses GPIO power sequencing and USB data/control/fastboot.

| Component | Current implementation | Remaining gap |
| --- | --- | --- |
| BCM2711 CPU, interrupts, clocks | QEMU Pi 4B, four Cortex-A72, TCG, 2 GiB | CM4 board identity, other RAM sizes, timing fidelity |
| SD and Linux | Official `kernel8.img`, CM4 DTB, actual ext4 root, qcow2 writes | Firmware/EEPROM boot chain; exact CM4 SD routing |
| UART | PL011 console, stdio or loopback TCP | Electrical/timing validation, UART Bluetooth assignment |
| Firmware GPIO expander | Stateful mailbox config/state for logical pins 128–135 | Electrical levels, externally driven inputs, board-specific reset defaults and dependent-device effects are not modeled |
| Keyboard/trackball | QEMU USB keyboard and relative mouse over DWC2 | STM32F103 firmware, composite descriptors, Fn layers, joystick, LEDs, CDC and DFU |
| Watchdog/power reset | Repository QEMU patch adds countdown/reload/cancel/expiry | Broader power-management register coverage |
| Display | 1280×720 logical surrogate through QEMU's mailbox framebuffer; local GTK/SDL or loopback VNC | VC4/V3D, DSI, CWU50 panel and OCP8178 backlight; no panel-fidelity claim |
| PMIC/battery | QEMU AXP221 at I2C address 0x34; Linux driver probes it | Regulators, battery state, ADC, charging and interrupt behavior |
| Networking | Optional QEMU USB RNDIS adapter and user-mode NAT/SSH forwarding | Actual Wi-Fi/BT hardware and radio behavior; guest configuration still required |
| Optional 4G | Not modeled | SIM7600 USB/AT/QMI/fastboot, GPIO power/reset and fault scenarios |
| Audio | Opt-in USB null/WAV playback, synthetic capture, or both with `usb-duplex` | Native PWM/codec, jack/speaker routing, host audio backends and physical fidelity |
| Expansion | Not modeled | Expansion hardware |

The real panel/PMIC wiring is in `clockworkpi-uconsole-overlay.dts` in the patched
kernel; the keyboard descriptors are in `Code/uconsole_keyboard/uconsole_keyboard.ino`.

Audio defaults to `none`. `run --managed --audio usb-duplex` attaches two
surrogate ALSA cards: playback recorded into a private workspace WAV, and
deterministic synthetic capture. Workbench and MCP expose the same mode.
Neither uses the host microphone/speakers or validates native analog audio.
Finalize recordings by stopping the guest cleanly. See
[audio control and evidence](forge-agents.md) for per-device hotplug readback,
partial failures, recording privacy and guest-driver limitations.
Neither generic USB input devices nor a host console count as running that MCU firmware.

## Build and prepare

Python **3.12+** is required for Workbench and the forge tools. The installed
launchers reject older interpreters, even when they provide Tk. On Ubuntu, source-build prerequisites are
`build-essential ninja-build pkg-config libglib2.0-dev libpixman-1-dev libslirp-dev`
and Python with venv support. Tk is needed only for the desktop workbench
(`python3-tk` on Ubuntu). QEMU's configure step reports any additional dependencies.

```sh
python3 tools/build_emulator_qemu.py
python3 tools/uconsole_emulator.py prepare /path/to/uConsole_CM4_v3.1_64bit.img.bz2
```

The bootstrap downloads QEMU 10.2.4 from the official HTTPS distribution site,
checks the repository-pinned SHA-256, applies the repository's watchdog,
upper-memory, AXP221 attachment/poweroff, GIC monitor-access and DWC2
remote-wakeup patches, and builds in
`build/emulator/`. It does not replace system QEMU. The archive hash records the
artifact used here; it is not a signature-verification mechanism.

Preparation verifies the image against the recorded official-image SHA-256,
decompresses sparsely, reads FAT boot files without mounting anything, and creates
`build/emulator/workspace/`. Keep this directory together: its overlay references
the absolute path of its backing image. A custom `.img` or `.bz2` requires its
expected hash via `prepare IMAGE --sha256 HASH`; it must retain the supported
CM4 image layout and filenames. An existing workspace is never replaced.

Allow approximately 15–20 GiB for source/build/base images and subsequent image
exports, depending on the image and guest writes. Multiple workspaces consume
additional space. All generated material is ignored by Git.

## Run, edit, and export an image

Normal and desktop boots initially mount root read-only so the guest's early
filesystem check can run, followed by its standard read-write remount.
Maintenance mode mounts root writable for explicit image editing; it does not
replace an offline filesystem check or recovery procedure.

```sh
# Root maintenance shell: boots the actual root filesystem without systemd.
python3 tools/uconsole_emulator.py run --mode maintenance

# Normal systemd boot, serial console and optional SSH port forwarding.
python3 tools/uconsole_emulator.py run --serial-port 4445 --ssh-port 2222

# Desktop boot on the 1280x720 surrogate display.
python3 tools/uconsole_emulator.py configure-display
python3 tools/uconsole_emulator.py run --mode desktop --display gtk --serial-port 4445

# Desktop source editor, serial console, agent context, tasks, files and export.
python3 tools/uconsole_workbench.py
```

Desktop mode masks the physical 4G and EEPROM services, plus the image's
Wayland VNC and Plymouth units. Those units otherwise wait for DRM devices that
the mailbox-framebuffer surrogate intentionally does not provide.
The overlay-local launcher starts Xorg/fbdev and the image's own RPD X session
for its configured autologin user; it does not modify the read-only base image.
Setup schema 16 keeps its Xorg configuration outside the native configuration
directory and leaves the physical display-manager selection untouched. The
surrogate service requires the `uconsole.emulator=1` boot flag, supplied only by
the desktop launch profile. Setup uses private Unix control sockets and retains
per-attempt logs without truncating the normal serial transcript.
The launcher delegates authentication, X authority and session lifecycle to the
image's LightDM. On emulator boots it creates a runtime-only configuration under
`/run/uconsole-emulator`, selecting Xorg/fbdev and RPD X and allowing a seat
without DRM. It rereads native autologin settings on every restart. Without the
emulator marker it executes LightDM with its ordinary arguments and configuration.
A conditional drop-in skips tty1 autologin only on emulator desktop boots;
LightDM's enabled state and the native display-manager selection remain intact.
Emulator boot arguments mask the absent DRM device dependencies, not LightDM.
The image's RPD X session retains its normal session name and launcher. A
conditional Xsession hook only runs on emulator boots of `rpd-x`. When its
LXSession configuration selects `lxpolkit` and that executable is available,
the adapter creates a private runtime XDG directory forwarding existing system
configuration entries and hiding the competing MATE PolicyKit autostart entry.
Forwarding preserves RPD programs that consult only the first configuration
directory. The runtime directory must be private and owned by the session user;
the generated profile disappears with that user's runtime directory. This follows the
[XDG autostart precedence rules](https://specifications.freedesktop.org/autostart/latest/).
Native boots do not activate this directory; system autostart files and user
configuration are not rewritten. A user's alternative agent selection is
preserved, and personal autostart overrides retain their higher priority.
Diagnostics are in `/var/log/lightdm/`. Live diagnostic testing establishes a
post-onboarding authenticated desktop session and one schema-13 input/reboot/
shutdown/export cycle with a clean read-only filesystem check. A schema-16
hot-update and LightDM restart also preserve the session identity, terminal
shortcut and theme with one PolicyKit agent. Fresh final-schema onboarding,
repeatable poweroff and physical boot qualification remain open; see the
[validation record](emulator-validation.md) for the limits of each check.

Earlier experimental overlays may have overwritten native display defaults
without a backup. Setup rejects those configurations rather than guessing the
original settings. Preserve their user changes and recover native configuration
from the original image before retrying, or prepare a fresh workspace. Physical
boot compatibility still requires the dual-target qualification in the plan.

On this Ubuntu host use `/usr/bin/python3 tools/uconsole_workbench.py`, since
that Python installation has Tk. Select text in either pane and press
**Copy selection**, Control-C, Command-C, or use the right-click menu. **Copy
boot log** copies the entire visible serial transcript. **Copy agent context**
places a clean Markdown bundle with workspace identity, runtime state, fidelity
limits and recent boot output on the system clipboard, ready to paste into a
chat. **Paste command** moves clipboard text into the line input without sending
it, so the command remains reviewable.

On macOS, if Python/Tk startup waits on the system's post-crash window-restoration
dialog, either answer that dialog or launch
`uconsole-workbench -ApplePersistenceIgnoreState YES`. This native AppKit option
applies only to that process; Workbench does not delete saved windows or change
persistent Python.app preferences. It is not a remedy for guest boot failures.

An installed Workbench copies the bundled keyboard source into
`$UCONSOLE_BUILD_DIR/projects/uconsole_keyboard` on first open. This is a
user-owned project, not a write into the installation prefix. Subsequent starts
preserve edits; a newer bundled source is reported without merging or overwriting
the project. Git checkouts continue editing their checked-in source directly.
Control-S (Command-S on macOS) saves the editor. Window titles identify the
selected workspace, so independent Workbench windows can be distinguished.

Keyboard firmware packages include `provenance.json`: source/build-recipe hashes,
FQBN, recorded Arduino CLI/core information and the binary hash. Packaging
rebuilds when the provenance is missing or does not match the current inputs,
instead of treating an existing nonempty binary as fresh. Release jobs transfer
the manifest with the common firmware build. These are integrity/provenance
records, not signed toolchain attestations or physical-flashing evidence.

The **Guest files** browser lists and downloads files through the maintenance
console. **Tasks** loads the checked-in `uconsole-tasks.json`; tasks use argument
arrays or explicit guest scripts and produce structured output. The display
selector can open QEMU's GTK or SDL framebuffer in a second window. This still
does not emulate DSI/VC4. The workbench does not yet have language servers or an
integrated GDB frontend. Its console accepts lines; use SSH or another terminal
client for full-screen terminal programs. Workbench uses private control/serial
sockets through the shared headless runtime; independent workspaces no longer
compete for its default control ports. Legacy CLI launches still use TCP ports
4444/4445 and require separate ports for each owner.

For the same owned runtime without the GUI:

```sh
python3 tools/uconsole_emulator.py --workspace /path/to/workspace run --managed --mode maintenance
```

The foreground command prints the runtime identity and private socket paths.
Use `tools/uconsole_agent.py --serial-socket PATH exec -- uname -a` for guest
commands, or `tools/uconsole_emulator.py control query-status --qmp-socket PATH
--runtime-id ID` for identity-checked monitor access. These paths are local
capabilities, not network services. The programmatic runtime additionally
serializes guest transactions, checks that its child is alive before control,
and supports file transfer and clean maintenance-mode shutdown. Separate
standalone agent CLI processes do not yet share that transaction scheduler;
do not issue concurrent guest commands through them. The initial
[MCP server and companion skill](forge-agents.md) add permission-scoped jobs
for external agents; cross-client attachment and running-job cancellation
remain unfinished. Interrupting a managed
foreground launch forcibly stops the guest; shut it down cleanly first when
preserving filesystem consistency matters.

### Checkpoints and recovery

On Linux and macOS, stop the guest cleanly before making or restoring a
checkpoint. The CLI and GUI now share a workspace lock for launches, display
setup and export; image lifecycle operations use that same lock.
The Workbench **Image** menu exposes checkpoint creation, confirmed restore,
interrupted-restore recovery and boot-file refresh. These operations and export
run as tracked background processes with per-operation logs. Workbench blocks
launching another VM or closing its window until the image operation finishes,
then reports success or failure in its status line.

```sh
python3 tools/uconsole_emulator.py --workspace /path/to/workspace checkpoint before-upgrade
python3 tools/uconsole_emulator.py --workspace /path/to/workspace restore before-upgrade
# Only needed if a restore was interrupted:
python3 tools/uconsole_emulator.py --workspace /path/to/workspace recover
```

Checkpoints contain a standalone qcow2 disk, the current host boot artifacts,
machine configuration and SHA-256 manifest. They consume additional disk space
but do not depend on the original backing image. Restore first creates a
`before-restore-*` safety checkpoint, then replaces the working state. It never
overwrites an existing named checkpoint. An interrupted restore blocks launches
until `recover` completes the journaled replacement. Failed staging directories
are retained for diagnosis; do not remove a restore journal to bypass recovery.

Keep external QEMU/image-editing processes stopped during lifecycle operations;
they do not participate in the forge's workspace lock. QEMU's disk lock is an
additional check, not a substitute for cooperative ownership. A stopped guest
does not itself prove a clean filesystem: shut down or sync/remount read-only
before stopping it. Checkpoints preserve the extracted host boot artifacts as
they currently stand; each actual CLI/GUI boot refreshes those artifacts from
the current guest disk before constructing the launch command.

### Guest kernel updates

Boot refresh reads the MBR and first FAT partition through `qemu-img`, including
overlay writes. It extracts the selected ARM64 Linux kernel, checks compressed
kernel integrity, regenerates the emulator DTB from the current guest CM4 DTB,
and records their hashes in `machine.json`. It never changes guest boot files.
To inspect/refresh a stopped workspace without booting:

```sh
python3 tools/uconsole_emulator.py --workspace /path/to/workspace refresh-boot
```

The supported firmware-selection subset includes CM4 model filters and
`kernel=`/`device_tree=` selections, using the documented
[Raspberry Pi configuration format](https://www.raspberrypi.com/documentation/computers/config_txt.html).
Includes, initramfs selection, custom OS prefixes and unknown filters currently
fail with a compatibility error. The first FAT partition must end within 2 GiB
and partition 2 must be Linux. This is not firmware/EEPROM emulation: physical
overlays, firmware hardware options and the physical kernel command line are
not reproduced by the emulator launch profile. `run --dry-run` only displays
the currently extracted configuration; it does not perform refresh or validate
the current guest disk. Updated-kernel boot and driver compatibility still
need runtime tests; passing file validation does not guarantee a usable kernel.

### Agent CLI

The same guest operations are available without the GUI for coding agents and CI:

```sh
python3 tools/uconsole_agent.py inspect
python3 tools/uconsole_agent.py context --output /tmp/uconsole-context.md
python3 tools/uconsole_agent.py tasks
python3 tools/uconsole_agent.py task guest-summary
python3 tools/uconsole_agent.py exec -- uname -a
python3 tools/uconsole_agent.py ls /etc
python3 tools/uconsole_agent.py put ./config /etc/example.conf
python3 tools/uconsole_agent.py get /var/log/boot.log ./boot.log
```

Commands return JSON where automation needs state or transfer metadata. Guest
commands and file operations require a maintenance shell on the selected serial
port. Transfers are checksum-verified and limited to 8 MiB; use SSH/SCP for
larger artifacts. The context command strips ANSI escapes and its own command
wrappers so an agent receives useful boot evidence rather than terminal noise.

To start a managed VM with an initial power profile:

```sh
python3 tools/uconsole_emulator.py --workspace /path/to/workspace run \
  --managed --mode maintenance --scenario docs/scenarios/battery-discharge.json
```

Profiles require exactly `schema: 1` and a `power` object. Supported fields are
`ac_present`, `battery_present`, `battery_voltage_uv`, `battery_capacity`,
`battery_current_ma` (positive charging, negative discharging),
`pmic_temperature_mc` (millidegrees C, quantized down to 100 mC),
`pmic_over_temperature` (explicit comparator-fault injection), and
`power_key_pressed`. Omitted fields default to AC present, battery absent,
zero voltage/current, 100 percent capacity, 25 degrees C PMIC temperature and
released key and no injected fault. Temperature is a sampled 12-bit ADC value,
not conversion timing, battery temperature or automatic thermal thresholds.
The independent fault input sets the PMIC over-temperature status and latches its
IRQ on entry. Clearing the fault does not acknowledge the interrupt; guest W1C
does. If the guest enables REG8F bit 2, asserting this fault abruptly removes
emulated power, even with the IRQ masked. Use disposable/checkpointed workspaces;
this is not a clean shutdown. No physical-target fault injection is performed.
Unknown fields,
duplicate keys, invalid types/ranges and inconsistent charging states fail.
The VM stays paused until model support and every value are verified; `--pause`
keeps it paused afterward. Per-run `scenario-*.json` evidence stays in the host
workspace, outside the deployable image. These are initial sampled states,
not timed scenarios or an autonomous battery simulation. Installed packages
include the example under `share/doc/uconsole-workbench/scenarios/`.

In Workbench, use **Load power profile** before starting the VM. The profile row
shows its filename and digest prefix; the console shows the full digest and
requested values. Loading takes a snapshot: select the file again after editing
it to use new values. **Clear profile** restores the default for the next boot.
The selection lasts for this Workbench session, not across application restarts.
Profile changes are rejected while a VM or image operation is active. Successful
startup prints the verified readback and evidence path in the console. The same
snapshot survives automatic surrogate-desktop preparation.

The **Live power** row reads or changes the currently owned VM, independently
of the next-boot profile. Choose a field, enter JSON `true`/`false` for booleans
or an integer for numeric samples, and use **Apply field**. **Read state** reports
all six fields. Results appear in the console and in unique host-workspace
`power-event-*.jsonl` records, with the runtime identity, requested operation
and outcome. Invalid input is rejected before mutation. Model conditions apply;
for example, charging requires a battery, AC and an enabled charger. Power-key
events may trigger guest shutdown policy. Readback failure does not roll back
a change. These are sequential model samples, not atomic guest snapshots,
physical timing proof, or timed replay. Live controls are disabled by validation
while shutdown is pending and never attach to another owner's VM.

**Run power schedule** selects a schema-1 event document such as
`docs/scenarios/ac-cycle.json` for the running VM. Replay runs off the UI thread,
using host elapsed time; the window and serial console continue polling. The
dedicated replay status distinguishes completion, failure and confirmed
cancellation. **Cancel replay** prevents future events, not earlier effects.
Wait for confirmation before closing or changing power; pause/resume, power-off
and live-field controls reject conflicting actions until replay is terminal.
The console links each run to its host-side `power-replay-*.jsonl` evidence.
This is the same bounded event engine used by MCP; see
[schedule semantics](forge-agents.md). It is not guest-virtual-time scheduling.

To modify an image from a host terminal:

```sh
python3 tools/uconsole_emulator.py run --mode maintenance --serial-port 4445
# In another terminal, once the root prompt appears:
python3 tools/uconsole_emulator.py put ./example.conf /etc/example.conf
```

The legacy emulator `put` command uses the existing root shell, uploads files up
to 1 MiB in bounded chunks,
verifies the SHA-256 in the guest, installs with mode 0644 and syncs. It replaces
the specified guest file. Only one client can own the serial connection; the
workbench releases its connection during its Copy to guest operation. For shell
inspection, mount `/proc` and `/sys` in maintenance mode as needed:

```sh
mount -t proc proc /proc
mount -t sysfs sys /sys
cat /etc/os-release
cat /proc/bus/input/devices
```

Before stopping a maintenance VM, mount `/proc` and `/sys` as above, then run
`sync && mount -o remount,ro /dev/mmcblk1p2 /` for the tested CM4 profile and wait
for success, then `python3 tools/uconsole_emulator.py control quit` on the host.
The workbench's Power off button performs that sequence and waits for the
guest acknowledgment, resolving the mounted root's device through procfs/sysfs.
An explicit source device avoids relying on udev-created UUID links, which do
not exist in the maintenance shell. In normal mode use `sudo poweroff` inside the guest.
`control quit` alone is a virtual power cut, not a clean guest shutdown.

```sh
python3 tools/uconsole_emulator.py export /path/to/new-uconsole.img
```

Export flattens a stopped overlay to a **new** raw image, prints its checksum,
and refuses existing destinations or an image currently held by QEMU. It does
not flash a physical drive. Before publication it checks the partition-2 ext4
superblock: unclean/error states, pending journal/orphan recovery and invalid
metadata checksums reject the export. The source overlay is not repaired or
modified. Preserve a checkpoint or forensic copy and recover a separate copy
before retrying. This bounded superblock check is **not a full fsck** and does
not establish application integrity or physical boot compatibility.
Export also preserves the original source-card capacity: QEMU's power-of-two
SD padding is removed only if it is all zero and no partition extends into it.
If the guest used that padding or expanded its partition layout, export refuses
to discard it; the overlay is retained. New workspaces record the source size.
Legacy workspaces with a pinned base checksum verify that base before deriving
its size; if it is missing or changed, re-import the original image. Metadata
without either source size or base checksum retains the older full-capacity
export behavior and does not establish that the image fits the original card.
New workspace directories are owner-only (0700), and imported base images and
raw exports are owner-readable/writable only (0600); they can contain device
credentials. This does not change permissions on existing workspaces.
The exported disk contains your guest changes;
emulator-only DTB/command-line changes stay outside the SD image. Firmware
`config.txt` is interpreted only for the supported kernel/DTB selection subset;
changing hardware display settings still requires hardware validation. Each
launch refreshes the extracted kernel and emulator DTB from the current overlay
and rejects unsupported boot profiles. A reboot inside an already running QEMU
does not perform that host-side refresh; stop and launch again after changing
guest boot artifacts.

## Debug/control interfaces and platform status

`run --gdb-port 1234 --pause` starts paused for a GDB remote connection.
`control query-status`, `control stop` and `control cont` use QMP.
`run --vnc-display 1` exposes QEMU's framebuffer at 127.0.0.1:5901; it does
**not** implement the uConsole DSI display. Network, QMP, GDB and serial listeners
bind only to loopback. `--ssh-port` creates a USB network substitute and forwards
to guest port 22; it does not enable SSH or provision credentials.

Use `run --display gtk` on Linux or `run --display cocoa` on macOS to open
the generic QEMU framebuffer locally; `--display sdl` is also available when
the selected QEMU build includes SDL. Workbench's display selector offers these
backends, and the desktop recorder selects Cocoa on macOS rather than assuming
GTK is installed. Use `qemu-system-aarch64 -display help` with your selected
binary to check its compiled backends. The guest's built-in `bcm2708_fb` driver is configured for
a 1280×720 logical display, matching the uConsole's landscape presentation. The
physical panel is natively 720×1280 and mounted with a 90-degree rotation; this
surrogate deliberately presents the already-rotated logical size and does not
exercise the DSI, panel-orientation or backlight drivers. `--keyboard-cdc-port
4550` adds a USB serial surrogate for
the STM32 CDC function, while QEMU's separate USB keyboard and mouse cover input.
`--modem-at-port 4551` adds one generic USB serial surrogate for AT-command
client development. These split devices deliberately do not claim the uConsole
STM32 composite descriptors, DFU behavior, or the SIM7600 USB/QMI/fastboot
contract.

For experimental AT-client tests, attach the deterministic engine after starting
QEMU with that modem port:

```sh
python3 tools/serve_modem_at.py --port 4551 --sim pin
```

The disposable simulated PIN is `1234`. This currently implements AT/echo,
CMEE, CPIN, CFUN (0/1/4), CSQ and registration query/report modes 0/1.
It does not access real SIMs or radio services; unsupported commands return
errors. Syntax follows the
[SIMCom AT manual](https://files.waveshare.com/wiki/SIM7600G-H/SIM7500_SIM7600_Series_AT_Command_Manual_V3.00.pdf).
The adapter connects only to loopback and exits on disconnect. Composite USB,
network activation, GNSS, SMS/calls, fastboot and Workbench-owned lifecycle are
not yet implemented by this engine.

## Compare with the physical CM4 uConsole

For an owned raspi4b VM, the watchdog observer prints JSON-line register samples
without changing guest state:

```sh
python3 tools/observe_emulator_watchdog.py --qmp-socket /path/to/private/qmp \
  --runtime-id uconsole-forge-EXACT_RUNTIME_ID --duration 30
```

Use the exact socket and identity from the runtime that launched the guest.
Every request checks that identity. Optional `--pause-below 3` explicitly
authorizes pausing when an armed watchdog has less than three seconds left;
the tool then records CPU registers and leaves the VM paused for investigation.
It never resumes, resets or stops the QEMU process. The threshold is diagnostic,
not a proof of a missed heartbeat: legitimate short watchdog deadlines can
also trigger it. Sampling duration is limited to 300 seconds; control requests
have their own socket timeout. This tool is not a physical-hardware verifier.

The inventory tool captures the same read-only probes from an emulator or real
machine. SSH must already be configured; authentication credentials are not
collected. Review captures before sharing: hardware serial numbers, network
addresses and kernel command-line values may be sensitive.

```sh
python3 tools/uconsole_hardware_probe.py capture emulator.json
python3 tools/uconsole_hardware_probe.py capture hardware.json --ssh user@uconsole.local
python3 tools/uconsole_hardware_probe.py compare emulator.json hardware.json
```

It records kernel, device-tree model, CPU, USB/input devices, network links,
power supplies, I2C, GPIO, modules and command line, preserving failures when an
optional command is absent. I2C inventory reads registered devices and drivers
from sysfs; it does not scan buses or send transactions to unknown addresses.
The comparison marks failed or missing probes incomplete, even if both captures
failed identically, and identifies changed probes without
pretending textual equality is hardware equivalence. A physical capture remains
pending until this machine has an authenticated SSH route to the booted unit.

| Host | Current status |
| --- | --- |
| Linux ARM64 (this machine) | QEMU built and CM4 maintenance boot exercised; Tk tested under Xvfb |
| Linux x86-64 | Same TCG design; not locally boot-tested |
| macOS ARM64 | Native patched QEMU build and packaged GUI-owned maintenance boot/duplex audio exercised; broader release gates remain in the device plan |
| Windows | Portable Python/Tk helpers and `.exe` selection only; not a supported forge package host. Private image import/export requires POSIX permissions and refuses before creating files; source bootstrap is POSIX-only; not boot-tested |

The CLI accepts global `--qemu`, `--qemu-img`, and `--workspace` arguments before
the subcommand. Host-tool CI runs on Linux/macOS/Windows; that is not a substitute
for a full boot/GUI qualification matrix, and no remote CI result is claimed yet.

## Emulator-only compatibility changes

The prepared DTB is a copy of the shipped **CM4** DTB, not a generic virtual-board
tree. It enables the DWC2 controller in host mode, disables the Bluetooth child
of UART0, and removes UART `skip-init` and hardware flow-control properties.
The shipped aliases name this PL011 console `ttyAMA1`. The direct kernel command
line selects it and identifies root by the image's MBR partition UUID.
The full uConsole carrier overlay is not applied because its DSI panel,
backlight and regulator graph still reference devices absent from QEMU. The
machine patch supplies the PMIC node directly. QEMU also removes several
unsupported BCM2711 nodes itself. Each difference limits hardware-validation
claims.

The [watchdog patch](../Code/patch/qemu/bcm2835-watchdog-timer.patch) fixes QEMU's
immediate-reset behavior when Linux arms its watchdog. The Linux driver uses
65536 ticks/second in a 20-bit register; a QEMU virtual-clock timer now implements
that countdown and reload/stop behavior, including Linux's poweroff encoding.
Migration state is version 2; old version-1 device states are not accepted.
This is a local patch, not an upstream-merged fix.

The [DWC2 wakeup patch](../Code/patch/qemu/dwc2-remote-wakeup.patch) corrects
remote keyboard/mouse wakeup from USB suspend. The original model raised a
port-change interrupt without any corresponding change bit, leaving Linux
unable to acknowledge it. The patch signals the dedicated wakeup interrupt
and lets the guest finish resume signaling. A diskless USB DMA regression
and a normal desktop input/reboot comparison are recorded in
[local validation](emulator-validation.md). This is not a claim of complete
USB suspend, power-management or physical timing fidelity.

The [upper-memory patch](../Code/patch/qemu/raspi4-upper-memory.patch) also fixes
the decision to add the upper RAM region to the device tree. It must use the
board's total RAM, not the already-limited boot memory size. Without this change
Linux sees only the lower region (about 917 MiB usable); with it the tested
kernel reports `MemTotal: 1902268 kB`.

The [AXP221 patch](../Code/patch/qemu/uconsole-axp221-pmic.patch) attaches QEMU's
existing AXP221 model at the uConsole address, enables the CM4 I2C controller in
the runtime tree, and describes AC and battery child functions. Linux identifies
the device as AXP221. The current QEMU model exposes its register file but does
not yet simulate a configurable battery, charge cycle, ADC readings, regulator
wiring, or PMIC interrupts.

## Validation and remaining implementation

```sh
python3 -m unittest discover -s tests -p 'test_emulator.py' -v
python3 tools/test_emulator_watchdog.py
python3 tools/test_emulator_gic.py
python3 tools/test_emulator_pmic.py
python3 tools/test_emulator_usb_wakeup.py
xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -p 'test_emulator_gui.py' -v
```

Tests cover image checksums/preservation, FAT extraction and cyclic chains,
DTB changes, launch isolation, destination protection, agent task/context
contracts, clipboard behavior, and real QEMU watchdog and PMIC MMIO behavior.
Local build and boot evidence is in
`build/emulator/`; see `emulator-validation.md` for observed results.

Full-device implementation still needs the DSI/VC4 and panel/backlight models,
AXP221 battery/charging extensions, STM32/USB composite firmware path, SIM7600 model,
audio and wireless coverage. Add each model against documented register or USB
contracts with driver-level tests and comparison captures from the physical CM4.
Do not turn missing hardware into permanently successful fake responses.

For a complete IDE, keep QMP, GDB, serial and image operations behind this host
tool boundary; next add project/build tasks, a real terminal component, debug
UI, image browsing/diffing, device scenario controls, and packaged installers.
Release qualification must include actual boot/edit/export/debug workflows on
Linux, macOS and Windows, alongside physical CM4 validation.
