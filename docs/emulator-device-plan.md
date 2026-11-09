# uConsole CM4 full-device emulation plan

## Goal and fidelity contract

The end state is a development machine that boots the official CM4 image and
exposes the same guest-visible buses, devices, state transitions and failure
modes as a CM4 installed in a uConsole.  A functional surrogate is acceptable
when it is explicitly identified as such.  A surrogate must not be used as
evidence that a physical protocol, timing constraint or electrical path works.

Every model therefore has three independently reported levels:

1. **Host surrogate**: enough guest-visible behavior for application work.
2. **Driver contract**: the production Linux driver binds and its observable
   interfaces behave like hardware, including errors and interrupts.
3. **Hardware fidelity**: register, USB, timing and power behavior is checked
   against captures from a physical CM4 uConsole.

The current emulator is level 1 for display, keyboard/mouse, networking and one
modem AT port; level 2 only for the tested part of the AXP221 identity/register
path and watchdog. It is not level 3 for any carrier peripheral.

## Build-upgrade qualification

Separate demo backlog: [live, code-linked uConsole schematic](workbench-demo-ideas.md).
This is a proposed Tk Canvas view of observed emulator state and activity, not
an electrical simulation or an additional gate for the help/diagnostics release.

Repeat-build correction (2026-09-26): the release rehearsal exposed an overlapping
patch-stack bug in cached QEMU trees. Reverse-checking an early patch against
files subsequently modified by later patches was not a valid cache check.
Preparation now applies the full stack in a temporary pristine tree, records
final hashes for every patched/model file, and publishes the completed source
atomically. Reuse verifies those final bytes without reapplying patches; changed
cache files are refused and preserved. A fresh Linux ARM64 build, immediate
repeat build and complete emulator-device checks passed. Package CI now requires
both initial and repeat builds from extracted resources on every native target.
Logs are `emulator-build-prepared-source-20260926.log`,
`emulator-rebuild-prepared-source-20260926.log` and
`emulator-check-prepared-source-20260926.log` under `build/emulator/`.
Earlier package results alone did not exercise this repeat-build failure.

Physical development defaults to an SSH host/target loop with a verified,
host-retained backup and an explicit restore path before every target mutation.
A spare SD card or reader is not a prerequisite. Successful changes may stay
deployed; qualification must also exercise restoration. Optional external-media
flashing is a separate deployment workflow, not the live-development path.
Boot-affecting changes require a recovery path that still works if SSH cannot
return; file/service rollback alone does not satisfy that requirement. Never
overwrite the target's mounted system card to simulate live image deployment.

Prepared recovery controls (2026-09-26): Workbench and MCP now share the
`target-recovery` permission and owner-pinned job policies, with a local review,
approval and execution panel. This is separate from ordinary `target-write`;
read-only clients never inherit either grant. Foreground jobs retain the durable
lease owner, serialize by workspace and physical machine, and cannot be
cancelled while running. A status-query failure retains the accepted job rather
than resubmitting it. Linux's full suite passes 1,463 tests plus shell checks;
native macOS passes all nine panel tests and all 44 Workbench GUI tests. Logs:
`build/emulator/full-tests-recovery-panel-20260926.log`,
`macos-recovery-panel-tests-20260926.log`, and
`macos-workbench-recovery-panel-tests-20260926.log`.

Offline health portability (2026-09-26): private backup/derivative copies can be
checked through inherited descriptors on Linux and macOS, without mounts or
repairs. Native FAT32/ext4 fixtures and all 26 focused tests pass on both hosts;
retained logs are `filesystem-portability-tests-20260926.log`,
`macos-filesystem-portability-tests-20260926.log`, and
`macos-filesystem-fd-probe-20260926.log` under `build/emulator/`. This closes the
Linux-only descriptor-path gap, not physical image deployment qualification.
The complete suites pass 1,465 tests plus shell checks on Linux and macOS
(79 explicit platform/tool skips on macOS), retained in
`full-tests-filesystem-portability-20260926.log` and
`macos-full-filesystem-portability-20260926.log` under `build/emulator/`.
Firmware staging and recovery-plan preparation still need integration into the
guided host/target flow. Prepared hold installation, release and reconciliation
now have distinct owner-approved jobs with independent root guards, but their
public workflow and subsequent physical boot still require qualification. The physical enhanced-image
deploy/verify/rollback loop and final release-wide evidence remain required.

Prepared hold workflow (2026-09-26): fixed `hash-card`, `prepare-hold`,
`install-hold`, `release-hold` and `reconcile-hold` jobs now use the same scoped
Workbench/MCP controller. Preparation derives the independent root guard from
an explicitly typed, pinned backup/export manifest; it cannot silently adopt
the live card's digest or approve its own resulting plan. The 1,475-test suites
and shell checks pass on Linux and macOS (79 explicit macOS skips), retained in
`full-tests-hold-jobs-r2-20260926.log` and `macos-full-hold-jobs-20260926.log`.
All 31 focused job/panel tests also pass natively on macOS. Pure host preparation
using retained physical hold/hash/source evidence passes in
`physical-hold-plan-authoring-20260926/`: target contact, lease opening and
dispatch were forbidden, and the resulting release draft remains unapproved.
That draft does not replace the live physical runner or qualify current card
state. Logs and records above are under `build/emulator/`.

Normal-SSH staging preparation (2026-09-26): Workbench's owner-only
**Prepare boot staging…** action now freezes publication/firmware pins, requires
an acknowledged intact private recovery image, backs up all nine boot preimages,
authors the four ordered phase drafts and hold review, and verifies unchanged
normal-boot/file/publication bookends. It does not grant client permissions,
approve policy, stage firmware or reboot. A failed preparation retains incomplete
artifacts. The complete Linux suite passes 1,486 tests plus shell checks in
`build/emulator/full-tests-staging-preparation-20260926.log`; all 20 focused
preparation/panel tests pass on Linux and macOS in
`recovery-staging-preparation-tests-20260926.log` and
`macos-staging-preparation-tests-20260926.log` under `build/emulator/`.
Physical execution of this public route and bootstrap provisioning remain open.

Fresh-session enrollment (2026-09-26): Workbench now provides an owner-only
**Enroll recovery session…** step after a separately authorized recovery boot.
It verifies pinned RAM identity/firmware selection and the staging-bound owner,
records a local unrenewed binding, and refuses duplicate per-boot enrollment.
It neither adopts an existing lease nor increases client grants. All 21 focused
tests pass on Linux and native macOS; see `docs/emulator-validation.md` for logs
and the complete-suite result. Physical enrollment through this new UI, recovery
image provisioning, boot orchestration and the complete guided loop remain open.

Native unchanged-source stream (2026-09-26): the retained physical runner has
acknowledged all 7,481 chunks (31,373,918,208 root bytes) and completed its
post-stream root/protected-range hashes. The receipt in
`build/emulator/physical-restore-stream-20260925/root-restore/restore-attempt/acceptance.json`
is `acknowledged`, reports `bytes_written=0`, and matches the original root
SHA-256 `43aec057497163618ef9e051e2146d41c944f99242af833c0e2bf66749db8da8`.
This proves the unchanged native stream, not actual root-write qualification,
enhanced deployment or filesystem health. Independent reconciliation, hold
release and normal return are still running; no overall acceptance is claimed.

Build-upgrade qualification (2026-09-25): the QEMU builder now isolates source
and build trees by frozen patch contents and recipe, retaining the previous
public build until both new binaries pass version checks. Legacy build
directories are retained during migration. Linux ARM64 migration, repeat build,
and the complete `make check-emulator` gate pass; logs are
`build/emulator/generation-build.log`, `generation-rebuild.log` and
`generation-check-emulator.log`. Nine source/cache tests cover safe extraction,
patch identity, legacy preservation, failed-publication rollback and failed-build
preservation, including actual patch revisions and a rejected patch. Native
macOS ARM64 migration and repeat build also pass (`macos-generation-build.log`,
`macos-generation-rebuild.log`); the retained legacy binary preserves SHA-256
`b3cc6f48af7443ecceb01195bf321370df0851d540791a9ea6c18271ea7b35bf`.
The new binary is `9757e6fade6deaee12e2227a5cfaf15ea61b1683c3fa7743f78fc466f894169c`.
The Python suites pass 670 tests on Linux and macOS (44 explicit Linux-only
skips on macOS), in `python-suite-build-generations-r2.log` and
`macos-python-suite-generations.log`. The macOS emulator gate passes in
`macos-generation-check-emulator-r2.log` after pausing dummy CPUs in the diskless
GPIO register test, retaining all its reset assertions and adding bounded
terminate/kill cleanup. Its first run hit the unresolved running-qtest-CPU reset
hang; the failed log and `macos-generation-gpio-stall.sample` are retained.
That QEMU core race is not fixed by changing the register-test harness.
Follow-up running-CPU lifecycle evidence distinguishes two failures: the GPIO
log completed its reset assertions and then hung during shutdown in
`pause_all_vcpus`; a dedicated stress test subsequently hung on `system_reset`
in process 0, cycle 8, after eight complete reset/stop/continue cycles on macOS.
`tools/test_emulator_cpu_lifecycle.py` retains each dispatch/acknowledgement and
process-exit result, fails on a timeout, and reaps its own child even when TERM
does not work. Evidence is in `cpu-lifecycle-baseline-20260925/` and its sibling
log. The identical Linux test passes 50 processes with ten cycles each in
`cpu-lifecycle-linux-baseline-20260925/`. This comparison establishes a local
macOS reproduction, not that Linux can never exhibit the issue. Three harness
tests cover running (not `-S`) launches, operation ordering, failed-run cleanup
and preservation of existing evidence. The stalled stack and pinned source
point to the dummy-CPU signal/condition wait path; the precise lost-wakeup
mechanism was not established by that initial investigation.
This does not establish migration through installed release archives.
Retained cache generations consume additional disk
space; toolchain changes are not a hermetically tracked input.

### Darwin dummy-CPU wakeup regression

Follow-up on 2026-09-25 replaces the Darwin dummy-thread signal wait with
QEMU's counted semaphore, paired with a dummy-accelerator kick callback and
initialization before thread creation. The patch is
`Code/patch/qemu/dummy-cpu-darwin-wakeup.patch`; TCG and HVF thread algorithms
are unchanged. Linux retains its existing signal wakeup path. CPU hot-unplug,
Xen and Windows are not qualified by these raspi4b tests.

An [upstream event-order correction](https://mid.mail-archive.com/qemu-devel@nongnu.org/msg1227739.html)
was tried first, but did not fix our macOS reset reproduction; its failed trial
is retained in `build/emulator/cpu-lifecycle-order-20260925/`. That candidate
is not in the active patch list. The semaphore trial initially completed 42
processes before encountering a distinct refused QMP reconnect. The harness
now holds one QMP connection for each process and treats missing acknowledgements
as failures, without resending commands.

The same persistent-connection test against the retained pre-fix Mac binary
hangs at process 1, cycle 1, during `stop`
(`cpu-lifecycle-baseline-persistent-20260925/`). The semaphore build passes
100 processes with 20 reset/stop/continue cycles each, including clean exit
of every process (`cpu-lifecycle-semaphore-r2-20260925/`). Linux also passes
100 processes with 20 cycles (`cpu-lifecycle-linux-semaphore-20260925/`).
Both complete emulator gates pass (`linux-check-emulator-dummy-wakeup.log`,
`macos-check-emulator-dummy-wakeup.log`), now including the running-CPU lifecycle
regression test. This addresses the reproduced hang; it does not claim an
exhaustive proof of host signal semantics or all accelerator lifecycles.
The final Python suites pass 675 tests on both hosts (44 Linux-only skips on
macOS), recorded in `linux-python-suite-dummy-wakeup-r2.log` and
`macos-python-suite-dummy-wakeup-r2.log`. The native Mac TCG guest/controller
thermal replay also passes (`thermal-dummy-wakeup-20260925/thermal-acceptance.json`):
the intentional thermal power removal is recorded as abrupt, the next replay
event is not attempted, and the base image remains unchanged. This is not a
clean-shutdown or physical-hardware qualification claim.

Qualified QEMU SHA-256 values:
Linux `f88a574b8b26d0e422b9b718774f5d5c8f393ac85362bcd35407540919bc3932`;
macOS `5e6eb133d3ace022f5338ce9b8bbaaf19d9f42c4dbb0ee18cf1b296dc0a2893c`.
The patch SHA-256 is
`237e34f0e02c3352e1ba873e2e70c9d4ef06b0ac85195fdab722864c19cad0a5`.

## Sources of truth

Implementation must be checked against all of these rather than inferred from
one device-tree file:

* `clockwork_Mainboard_V3.14_V5_Schematic.pdf`: AXP228 power tree, PWM audio and
  jack switching, carrier Wi-Fi/Bluetooth, display routing, USB hub/ports and
  the 200-pin core connector.
* `clockwork_Adapter_CM4_Schematic.pdf`: the CM4-to-core-connector mapping,
  native CM4 USB, DSI, HDMI, SD/eMMC, GPIO, camera and power signals.
* `clockwork_UC_4G_Schematic.pdf`: SIM7600G-H USB/UART/GNSS/power controls,
  NAU8810 PCM audio codec, headset path and extension connector.
* `keyboard_220816.pdf`: GD32F103Rx, 8x8 keyboard matrix, 17 direct buttons,
  four trackball quadrature inputs, OCP8178 keyboard backlight and USB wiring.
* `Code/uconsole_keyboard/`: the exact composite HID/CDC firmware behavior,
  key layers, consumer controls, gamepad mapping, trackball acceleration/glide,
  scroll mode, debouncing and three-level backlight control.
* The current kernel's `clockworkpi-uconsole-overlay.dts` and
  `panel-cwu50.c`: Linux-visible topology, regulator graph, GPIO assignments,
  720x1280 DSI timing and panel command sequence.
* `Code/scripts/uconsole-4g-*`, modem guides and firmware manifest: modem power
  sequencing, five serial functions, RNDIS/QMI modes, AT behavior and fastboot
  transition/update requirements.
* Captures made by `tools/uconsole_hardware_probe.py` on both the emulator and a
  physical unit. Schematics describe connectivity; only captures establish the
  populated revision and runtime behavior.

## CM4-facing topology and required models

| Subsystem | Physical contract | First useful model | Driver/fidelity completion |
| --- | --- | --- | --- |
| Internal display | DSI1, CWU50 720x1280 panel mounted at 90 degrees, GPIO8 reset, GPIO9 OCP8178 backlight, AXP ALDO2 supply | Delivered 1280x720 mailbox-framebuffer surrogate through GTK/SDL/VNC | BCM2711 DSI host/interrupts, panel DCS state machine, reset/supply ordering, backlight pulse protocol, scanout comparison |
| Power | AXP228 wired and described to Linux as AXP221 at I2C0 address `0x34`, IRQ on GPIO2; AC, battery and regulator children | Existing QEMU AXP221 identity/register file | Writable regulators, AC/battery/ADC state, charging transitions, IRQs, power key and orderly poweroff/reset |
| Battery measurement | Two-cell-parallel simple battery profile; ADC101C at I2C1 address `0x54` | Static configurable voltage/capacity scenario | Conversion timing, threshold/alert behavior and correlation with AXP power-supply values |
| Keyboard deck | GD32F103Rx USB composite device; keyboard, consumer, joystick and mouse HID reports plus CDC; DFU/reset re-enumeration | One QEMU composite USB device driven by QMP matrix/trackball controls | Exact descriptors, firmware state machine, CDC, bootloader/DFU lifecycle; optional GD32 firmware co-simulation |
| Keyboard inputs | 8x8 matrix, 17 direct buttons, four quadrature trackball inputs | Logical key/button/trackball injection | Debounce, Fn/layer locks, joystick mode, consumer keys, glide/wheel behavior and rollover checked against firmware |
| Keyboard lighting | PA8 PWM into OCP8178, firmware levels 0/500/2000 | Expose state and QMP control/inspection | PWM/pulse timing and brightness measured against hardware |
| USB carrier | CM4 USB2 through the carrier hub to keyboard, external ports and optional extension | Existing DWC2 plus explicit QEMU USB hub topology | Port power/hotplug/over-current and physical enumeration tree comparison |
| Audio | CM4 firmware/PWM stereo into carrier filtering/switching, dual AW8110 amplifiers; GPIO10 jack detect and GPIO11 speaker enable | QEMU USB-audio surrogate with host backend plus GPIO jack/speaker scenario | BCM2835 audio/VCHIQ or PWM path, jack detection, amplifier mute and routing behavior |
| Wi-Fi/network | CM4 on-module radio for wireless CM4 variants; carrier RTL8723-class block is revision/core dependent | Existing USB RNDIS/NAT substitute | Confirm populated CM4 path, then SDIO brcmfmac and UART HCI contracts or explicitly retain radio surrogate |
| Bluetooth | CM4 UART-attached controller and firmware handshake on wireless variants | Host-backed virtual HCI with controllable peers | UART flow control, rfkill/power sequencing and suspend/wake behavior |
| 4G extension | SIM7600G-H composite USB, GPIO24 power and GPIO15 PWRKEY sequence, five ttyUSB functions, RNDIS/QMI, GNSS and fastboot; optional NAU8810 PCM audio | Composite QEMU USB modem with scripted AT service and user-mode NAT | Correct USB identities/modes, ModemManager contract, GNSS scenarios, calls/SMS, disconnect faults and fastboot partition protocol |
| HDMI | CM4 HDMI0 routed through carrier protection/connector, HPD and CEC | Map the same surrogate scanout to an optional second QEMU display | HPD/EDID/CEC and HDMI audio only if software under test needs the physical contract |
| Storage | CM4 SD/eMMC selection and removable microSD carrier path | Existing raw base plus qcow2 SD overlay | Card removal/write-protect/fault scenarios; EEPROM/eMMC only when boot-chain testing is added |
| Expansion | SPI4 with CE on GPIO4, UART1 on GPIO14/15, I2C1, GPIO and camera lanes on extension connectors | Socket-backed SPI/UART/I2C test peripherals | Per-device models added only from a named extension-board contract |

The mainboard schematic supports several compute cores. Before modeling its
RTL8723-class radio or any core-specific signal as a CM4 attachment, capture the
actual CM4 USB/SDIO/UART device tree and enumeration. Unpopulated or bypassed
circuits must not appear merely because they exist on the shared carrier.

## Forge architecture: one image, two boot environments

Workbench is an image-development forge, not just an emulator frontend. Users
must be able to modify a supported device image, test it in the IDE, export it,
and run that same exported image on the corresponding physical uConsole. Do not
maintain a separate emulator-only distribution or discard user enhancements on
export. This is a target contract, not a capability already qualified today.

Separate three kinds of state:

* **User image state:** applications, source, packages, services, configuration,
  and intentional kernel changes. These persist through export and reimport.
* **Environment adapters:** guest enhancements for surrogate display, transport,
  and development access. These may ship in the shared image, but must activate
  only through an explicit emulator boot profile. Physical boot keeps its native
  display, input, networking, and service defaults. Development access must not
  silently become a root service exposed on physical hardware.
* **Host workspace state:** QEMU, generated emulator DTBs, launch arguments,
  scenarios, logs, checkpoints, and build metadata. These belong outside the
  deployable image; a companion manifest records their versions and provenance.

Use an explicit emulator kernel-command-line marker and conditional startup
configuration for adapters. Pass surrogate Xorg configuration only to that
session, rather than installing a global hardware-affecting default. Do not
replace the physical display-manager selection. Migration must restore only
configuration known to have been changed by the forge, preserving later user
edits and reporting ambiguous cases rather than guessing.

The working qcow2 overlay and exported raw SD image are different storage
containers for the same guest state. Export must require a stopped, consistent
guest and preserve the physical boot partition and its configuration. Reimport
must accept the exported image with recorded identity and supported layout.
Refresh the host's extracted kernel and generated emulator DTB whenever guest
boot artifacts change; otherwise the IDE can silently test a stale kernel.
Unsupported kernel or layout changes must produce a clear compatibility error,
not silently substitute a different kernel. Arbitrary user changes cannot be
guaranteed bootable: record separate emulator and physical validation results.

Acceptance follows one artifact through the full loop: import an official
image, add an application and a service, checkpoint, test in the IDE, export,
boot that export on hardware, verify the changes and native desktop, then
reimport and repeat the emulator checks. Emulator tests alone do not establish
physical boot compatibility. Keyboard MCU firmware remains a separate artifact;
it is not the Linux guest disk image.

### Default hardware workflow: live SSH target with backup and restore

#### Physical recovery diagnostics update (2026-09-25)

The native RAM recovery image now includes the dynamically requested
`brcmfmac_wcc`, `brcmfmac_cyw`, and `brcmfmac_bca` modules when the selected
kernel supplies them. Ordinary dependency resolution of `brcmfmac` did not
include these plugins. A retained physical boot log proved that missing `wcc`
prevented interface creation; the corrected image initializes the CM4 Wi-Fi
firmware. This does not yet qualify association, recovery SSH, or restoration.

RAM console retention was verified with an exact per-boot marker in
`build/emulator/physical-ramoops-priority-trial-20260925/`. The subsequent
intentional failed-root trial retained its unique nonce, `root=/dev/ram0`,
`panic=0`, and the expected VFS panic, then returned to a verified normal boot.
The owner confirmed that no manual power cycle was used. Evidence is in
`build/emulator/physical-failed-root-20260925/`, including
`user-observation.json`. All nine trial boot-file preimages were restored.
This qualifies the observed failed-root fallback path, not arbitrary firmware
hangs, persistent recovery hold, or interrupted root restoration.

The network-observer trial in
`build/emulator/physical-network-observer-trial-20260925/` retained six bounded
samples: the interface exists, but no association event, IPv4 address, or DHCP
lease is observed. The log records a reboot at about 304 seconds. Normal SSH
returned after the host observer's timeout; that timeout must not be treated as
proof that the target remained hung. The subsequent effective-PMF trial below
establishes association and recovery SSH for this target and access point.
Diagnostics retain boolean status flags, not WPA credentials or SSIDs.

The effective-PMF trial in
`build/emulator/physical-effective-pmf-trial-20260925/` reached pinned recovery
SSH and verified RAM-root identity plus the native SD layout. Its boot nonce is
`dbc595f80f0e4e548c6cc90fe8542a18`; `recovery-ready.json` and
`recovery-storage.json` retain the observations. No block filesystem was
mounted and no root-partition writes were authorized. The Wi-Fi compiler now
resolves the profile's inherited PMF default against captured merged
NetworkManager configuration instead of unconditionally requiring PMF. Explicit
profile policies remain preserved; unsupported global overrides fail closed.
This establishes recovery connectivity, not full-card backup, restoration,
persistent hold, or release readiness.
The same trial subsequently returned to the verified normal system without
another reboot command. Its paired restore transactions restored all nine
boot-file preimages; `acceptance.json` records the completed comparison and
absence of root-partition writes. The private boot-mount policy and retained
RAM-console configuration remain in place for further qualification.
The refreshed Linux suite passes all 944 tests in
`build/emulator/full-tests-effective-pmf-20260925.log`.

The next backup prerequisite is a renewable recovery lease. The protocol core
in `tools/forge_recovery_lease.py` binds renewals to the RAM boot ID, trial nonce,
and a fresh host owner token; it accepts only the read-only `offline-backup`
purpose. Receiver-monotonic deadlines, sequential requests, non-extending
duplicate acknowledgements, five-minute grants and a 24-hour absolute cap are
covered by `tests/test_recovery_lease.py`. It supplies no transport or write
authority and is deliberately not connected to the physical image yet.
Production integration must authenticate requests through pinned recovery SSH,
initialize the lease at recovery startup (not first host contact), make both
the software reboot timer and hardware keeper enforce the same deadline, and
prove transport-loss expiry in QEMU before another physical long-backup trial.
Restore leases additionally require verified persistent recovery selection;
the read-only lease must never be reused as restore authorization.
`tools/forge_recovery_lease_socket.py` now provides the local Linux endpoint
for that future SSH helper: private exclusive socket binding, kernel peer-UID
checks, bounded packet size and receive time, strict duplicate-field rejection,
and terminal failure on uncertain deadline publication. Real socket tests
cover dropped replies and non-extending retries as well as malformed, idle,
wrong-peer and expired requests. This endpoint is not yet installed or started
by the builder; it does not replace either production timer.
The shared deadline exchange in `tools/forge_recovery_deadline.py` provides
private directory-fd anchored reads and atomic publication, exclusive startup,
session and monotonic bounds checks, and rejection of conflicting sequences,
malformed metadata, symlinks and nonfinite time values. Independent readers
retain their previously accepted state. These are local component tests, not
proof that either production timer has consumed a renewal; timer integration
and QEMU expiry qualification remain required before hardware deployment.
`tools/forge_recovery_lease_timer.py` composes the renewal endpoint with the
deadline publisher and supplies an independent software expiry consumer.
Tests send a renewal through the real local socket and read its publication
from a separately spawned process, then close the service and advance the
test clock to demonstrate expiry at the last granted deadline. Duplicate
requests do not extend that deadline; corrupt state, backward clock motion,
slow reads crossing expiry, and wait failures invoke the expiry action once.
The tests inject an expiry callback and clock: they do not prove a real reboot
or hardware-watchdog handoff. The builder still starts the original bounded
timers until the hardware consumer and QEMU qualification are complete.
The hardware consumer in `tools/forge_recovery_lease_watchdog.py` now reads
the same deadline through an independent timer instance before every feed.
Its Linux ARM64 adapter rechecks local RAM identity and explicit watchdog
selection, matches the character device against sysfs, verifies the BCM2835
identity/capabilities, and reads back the configured timeout. Expiry, missing
state, ioctl failure and interruption close the descriptor without magic-close
or disable operations. Fake-device tests cover these paths and a compiled
header-only ABI check verifies ioctl constants. This is not physical watchdog
qualification: launcher wiring, disposable QEMU renewal/expiry tests, and a
fresh native recovery-image trial still remain.
The builder now bundles the lease modules and an isolated-Python launcher.
Only `uconsole.recovery_lease=1`, a unique 64-hex `uconsole.recovery_owner`,
and explicit watchdog selection enable the new path. The launcher verifies
the local RAM session before side effects, exclusively creates session state,
forks independent software/hardware consumers, and requires readiness from
both before publishing its ready files. The init startup guard remains until
that handoff; ordinary boots retain the existing five-minute timer and native
C keeper. Unit tests cover binding, child readiness/EOF/timeout, resource
bundling and shell syntax. No new image has yet been built or booted with this
launcher, so QEMU and physical lease qualification remain open.
The disposable native build was subsequently dispatched to
`/var/tmp/forge-lease-build.x9skDT/disposable` on the uConsole. Preflight now
compares all bundled lease-module bytes and imports the launcher inside the
extracted root. Completion has not yet been observed: new SSH connections
timed out while the original build SSH process remained alive. Do not restart
this build or reboot the target on that evidence alone. The QEMU validator now
has `--require-lease` for a bound renewal, non-extending retry, and subsequent
software expiry after renewals cease. This path has unit coverage but has not
yet run against the pending image; it explicitly does not claim hardware
watchdog expiry from a software reboot.
While that build remained unconfirmed, launcher review found and fixed a child
fall-through path: a returning timer or an exception during child cleanup must
terminate with `_exit`, never enter parent supervision or unlink shared state.
Real-fork tests exercise both normal return and cleanup failure. The pending
native build predates this correction and cannot qualify the corrected source;
retain its result separately and build a fresh candidate after connectivity
returns. No physical reboot or deployment was requested during this build.
The host-side `BackupLeaseClient` now uses pinned recovery SSH, binds each
request and receipt to the expected boot/nonce/owner, preserves an unacknowledged
request exactly for retry, and rechecks RAM identity before accepting the
receipt. It rejects write authority, deadline reset and malformed replies.
The QEMU lease validator uses this client for its first renewal and sends an
identical raw retry to prove non-extension. Local tests cover lost replies and
post-renewal identity failure; guest qualification is still pending connectivity
and a fresh native image. The preceding full Linux suite passed 991 tests in
`build/emulator/full-tests-lease-launcher-20260925.log` before this client addition.
The original native-build SSH transport subsequently terminated with exit 255
(`Broken pipe`). This establishes transport loss only, not whether the remote
build completed or stopped. Inspect its remote processes and retained output
before dispatching any replacement build when the target is reachable again.
The target subsequently returned with boot ID
`802ccd65-fe60-4314-9ae4-0d1e00723b6d`; no old build process remained, its image
was zero bytes and it had no acceptance record. That interrupted attempt was
preserved. A new native build under `/var/tmp/forge-lease-r2.UKM4Iy/disposable`
completed preflight with the corrected launcher. Its 46,130,121-byte image has
SHA256 `c3ccbd8cd3d52931b94584460e0b1b5d66d2ee36441076428b07dca6148fa2d2`.
The completed transfer was independently hashed on the host under
`build/emulator/recovery-lease-native-r2-20260925/`. The disposable QEMU trial
in `build/emulator/recovery-lease-guest-r2-20260925/` has been launched with
Python identity, synthetic storage and renewable-lease checks; its terminal
acceptance result, not guest SSH alone, is required before qualification.
The separate modeled-watchdog-loss trial in
`build/emulator/recovery-lease-watchdog-guest-20260925/` passed using that exact
image. A pinned-SSH renewal succeeded and an identical retry did not extend it.
The emulator-only injector verified the launcher process tree, stopped the
supervisor and software timer, then killed the keeper. QEMU exited successfully
after 15.662 seconds without a software reboot, panic, or external termination;
the synthetic SD checksum remained unchanged. This qualifies the modeled
watchdog path, not physical hardware or interrupted root restoration. The
independent no-renewal software-expiry trial remains pending its terminal
acceptance record. All 144 focused recovery tests pass after validator changes.
That software-expiry trial subsequently passed: the accepted deadline was
321.320897696 guest-monotonic seconds, and stopping renewal led to a software
reboot after 298.903 host seconds (298.774 seconds remained at observation).
No forced cleanup was required and synthetic storage was unchanged. This is
separate from the modeled watchdog-loss result above; neither qualifies the
physical device's renewable lease yet.

Root backup now optionally accepts a lease bound to the exact recovery probe
and boot. A host-monotonic renewal pulse runs during transfer and independent
gzip readback, propagating lease errors and retaining incomplete artifacts.
Unleased transfers keep their 240-second cap; explicitly leased transfers are
bounded to 23 hours, below the guest's 24-hour absolute session cap. Unit tests
cover binding rejection, lost renewal, retained partial files and pulse timing.
The combined leased-backup path still needs disposable-guest and physical
qualification; no root restoration or whole-system backup is implied.
The combined disposable-guest test in
`build/emulator/recovery-leased-backup-guest-20260925/` now passes. It rejects
and retains a size-capped partial backup, observes release of the exclusive
partition claim, and verifies a fresh 58,720,256-byte root extent against the
host fixture hash `fc6b00d0852042694de3cd76c2794a54660c73e3fd6828cddf630844fc181dd9`.
The bound lease advances across attempts; an identical retry does not extend
it. Subsequent keeper-loss injection yields modeled watchdog expiry after
15.732 seconds, with no forced cleanup and no synthetic SD changes. This
small fixture does not prove sustained renewal over a full-size physical
backup, filesystem consistency, boot/metadata backup, or restoration.
A six-minute sustained-renewal validator mode now retains individual receipts
and requires the same guest boot to outlive its original deadline by at least
20 guest seconds before backup. The trial under
`build/emulator/recovery-lease-soak-guest-20260925/` is running; its final
acceptance remains required. Physical trial recipe compilation now accepts an
explicit validated `lease_owner` token, adds lease-specific qualification gates,
and preserves read-only backup scope. Normal recipes remain unchanged, while
native preimages already containing lease options are rejected. No physical
trial using these new recipe options has been dispatched.
Private physical-image preparation is now running under
`/var/tmp/forge-lease-r2.UKM4Iy/physical`. Its first capture attempt correctly
rejected a user-owned parent before credential creation; the revised helper
creates an exclusive root-owned private subdirectory, recaptures the approved
active Wi-Fi profile with machine/profile checks, and retains the dedicated
recovery SSH key pair. This is build preparation only: no publication,
selector changes or physical reboot have been dispatched. Its build result
and the sustained QEMU trial remain pending terminal evidence.
The private build has completed: image SHA256
`3b1742909f43d3894e5b5a65055776dd815ecb54ceb688e7bf3229968620bec4`,
46,131,216 bytes, retained under
`build/emulator/recovery-lease-private-20260925/private/`. Host transfer hashing
matches the native manifest. The validator now supports an independently pinned
Ed25519 server key, separate from the recovery client key. The exact private
image passed the disposable QEMU test in
`build/emulator/recovery-private-lease-guest-20260925/`: bound leased backup,
preserved interruption evidence, independent root hash verification, unchanged
synthetic storage and modeled watchdog expiry after 15.660 seconds. This image
contains real credentials and remains private; it is not a release artifact.
All 149 focused recovery tests pass. Physical lease qualification is still open.
The sustained disposable QEMU trial has now completed successfully: 13 renewal
samples retained the same boot to guest time 380.410, beyond its initial
307.971-second deadline. Interrupted/fresh leased backups then passed, followed
by modeled watchdog expiry after 15.668 seconds; no forced cleanup occurred.

Fresh physical trial preimages and reversible plans are retained in
`build/emulator/physical-renewable-lease-trial-20260925/`, nonce
`2b37becb14e643899ef5559c7cf3f03a`. Publication plan
`b2794eef92e5fbe01a9cc4054f0f00331993914b121fcfe364ff913aba31ab87`
binds the QEMU-tested private image to an absent destination under the verified
private boot-mount policy. Publication was dispatched through its paired
journal and completed. Fresh inspection confirmed all 46,131,216 image bytes
match, destination metadata remains private/root-owned, staging scratch is
absent and boot identity is unchanged. The publication receipt and inspection
are retained in `recovery-lease-private-20260925/publication-verified.json`.
No selector or physical reboot has yet been dispatched for this trial.
The prepared physical trial has now been dispatched by the retained
`physical-renewable-lease-trial-20260925/run_trial.py` harness. It acknowledged
all four staging transactions, verified their final guarded contents and issued
exactly one `reboot "0 tryboot"`. The harness will inspect RAM identity/SD
layout, renew for six minutes, stop renewal, then verify normal return and
restore all nine preimages. Its scope excludes root backup, root writes,
persistent hold and physical watchdog fault injection. Qualification remains
pending its terminal evidence; do not rerun the harness while it is active.
The physical trial reached verified RAM-root identity, matched SD geometry and
acknowledged lease renewal. Its six-minute renewal and subsequent automatic
return are still being observed by the same harness. Separately, the backup
tests now inject renewal loss after a real producer has emitted bytes and
verify worker termination, closed pipes and retained partial output; all eight
focused backup tests pass. This does not substitute for physical trial
completion or a full-size backup.
The refreshed Linux suite passed 1,004 tests in
`build/emulator/full-tests-renewable-backup-20260925.log`; the additional
mid-stream lease-loss test passed separately afterward. A fresh native macOS
snapshot at `/tmp/uconsole-lease-regression.9bwK6a` was transferred and tested
with the supported Python and process-local AppKit persistence option. Its
result is retained in `build/emulator/macos-lease-regression-20260925.log`:
1,005 tests completed successfully with 68 explicit platform skips in 68.167
seconds. This does not substitute for packaged native-client or hardware gates.
The physical lease trial has now verified sustained recovery in boot
`eb93d392-f2ae-4bc7-9457-6aa57bcd9be3`: guest time 388.965 exceeded the original
305.900 deadline. The same harness has stopped renewing and is awaiting normal
return and restoration; those gates remain pending.

The read-only backup API now offers an explicit whole-card byte scope, retaining
partition table, boot partition and root data in one archive. The worker checks
the source against verified geometry and still opens it read-only/exclusively;
no restore API or whole-card write authority is added. Status distinguishes
verified card bytes from a qualified consistent/bootable whole-system backup.
All 151 focused recovery tests pass. The synthetic-card QEMU trial under
`build/emulator/recovery-whole-card-guest-20260925/` is running to compare both
root-only and whole-card backups with independent host hashes.
That QEMU trial completed successfully. The complete 67,108,864-byte synthetic
card matches SHA256
`948cc30d738f0f1e7acabf0ce59bb01277796aa3c5cc59130394864d451752d9`,
and its root-only archive independently matches the expected root extent.
Both operations used bound leases. Subsequent modeled watchdog expiry took
15.743 seconds; no forced cleanup or synthetic storage changes occurred.
Physical full-card capture and filesystem/restore qualification remain open.
Backup now checks host free space against the configured compressed archive
bound plus a 128 MiB reserve before creating its output directory or starting
the streaming worker. The per-chunk reserve check remains active throughout
transfer. Tests verify rejection before worker creation; all 152 focused
recovery tests pass. This preflight does not reserve filesystem blocks or
replace the ongoing free-space guard.
The physical renewable-lease trial has now completed with
`soak_verified=true`, no renewal failure, verified normal return and all nine
boot-file preimages restored. No root-partition write was performed. Its
acceptance and retained RAM console are under
`build/emulator/physical-renewable-lease-trial-20260925/`. This closes the
observed physical sustained-lease/return path, not full-card backup or restore
qualification. The private publication and diagnostic/private-mount policies
remain retained for the subsequent backup milestone.
Full-size physical capture is now dispatched under
`build/emulator/physical-full-card-backup-20260925/`, nonce
`de00c55e8bed4dc5916afae09b87e1eb`. Fresh nine-file preimages and four pinned
staging transactions were captured and verified before one recovery reboot.
The retained `run_backup.py` harness will check the physical CID/layout, read
all 31,914,983,424 card bytes into a private host gzip archive while renewing
the qualified lease, verify decompressed length/hash, then stop renewal and
restore staging after normal return. Its scope is read-only capture, not a
restore test; filesystem consistency and restoration remain unqualified. Do
not restart the capture merely because it takes time or output pauses.
The card capture has now completed and independently verified all
31,914,983,424 uncompressed bytes against source SHA256
`1c958385a34ff97119e1a99b93a94330d6b5595c234dc7ce70d175cd29816fdc`.
The private gzip archive is 5,794,894,127 bytes. The harness stopped renewing
after verification and is awaiting normal return and boot-staging restoration.
Host-only extraction/filesystem checks are running against that completed
archive. This establishes physical byte-backup completion, not filesystem,
restore or normal-return qualification yet.
The same harness subsequently completed normal-return verification and restored
all nine boot-file preimages. Its final acceptance records verified card bytes,
normal return and preimage restoration, with no root-partition writes and no
restore qualification. `retained-ram-console.txt` contains the exact trial
nonce and reboot marker. No additional reboot command was sent.

Host validation reproduced the complete card checksum. The boot FAT check
passed (exit 0), but ext4 checking returned 4 with low-deletion-time/orphan-list
warnings. The failed validation remains retained under `offline-validation/`;
the original archive and all partition copies are unchanged. Read-only debugfs
inspection found all 105 flagged inodes unallocated, unlinked and with zero
blocks; every deletion timestamp is 3 or 4 seconds after the Unix epoch.
`root-diagnostics.json` retains the individual observations. The matching
[e2fsck 1.47.0 source](https://github.com/tytso/e2fsprogs/blob/v1.47.0/e2fsck/pass1.c#L1368-L1375)
flags nonzero deletion times below the inode count as possible orphan-list
references. An early-clock timestamp artifact is therefore plausible, but the
strict filesystem-health gate remains failed. No repair has been made to the
host baseline or the physical device, and byte-exact backup evidence is not
being relabeled as a clean-filesystem or restore qualification.

`validate_low_dtime_fixture.py` reproduces the same warning on a disposable
32-MiB ext4 filesystem: creating and deleting an empty file leaves an unused
inode; assigning deletion time 4 makes the unmodified checker return 4 with
the same orphan-list message. Repairing a separate fixture copy with an undo
log then passes a fresh read-only check, and replaying that undo on a third
copy restores the exact original hash. The untouched fixture baseline retains
its original bytes. Evidence is `build/emulator/low-dtime-fixture-20260925/`;
three focused tests pass, including output collision and failed-command
retention. This establishes the timestamp mechanism, not proof of how the
physical card acquired those timestamps. The same copy-only repair/undo
experiment is running under the physical capture's `host-copy-repair/`; no
physical repair or baseline replacement is authorized by this experiment.
That physical-snapshot copy experiment completed successfully. The untouched
root-partition baseline SHA256 is
`c1ebdf0e7291bc4a63442ab3ad17d145f51ecb9a7d8c407dd2fcfd12a0deb96b`.
The separately repaired copy passes `e2fsck -f -n`; its SHA256 is
`cfa2b01c54320995446cbca04c7d6f75b66955892435b0aa0587987d3129c7c0`.
The repair reported the same 105 known inodes. Applying its undo log to another
copy reproduced the exact baseline hash, and independent rehashing confirmed
the original host snapshot remained unchanged. `host-copy-repair/acceptance.json`
records this copy-only result and explicitly withholds physical restore
qualification. Do not replace the faithful backup with the repaired derivative
or report the original filesystem-health check as passing. The fixture tests
also pass on macOS, with the actual ext4-tool test explicitly skipped where
those tools are unavailable (`build/emulator/macos-low-dtime-fixture-20260925.log`).

Recovery boot-filesystem prerequisite (2026-09-25): the builder explicitly
includes `vfat`, `nls_cp437` and `nls_ascii` dependencies without automounting
anything. Native preflight resolves each as built-in for this target's
`6.12.62-v8+` kernel. The fresh disposable-credential image under
`build/emulator/recovery-bootfs-native-20260925/` has SHA256
`3c33aab7e8b4b2bbdd5744f8253fd8bb44554194f7ef2c5dcde748425384d975`.
It was built in `/var/tmp/forge-bootfs-build.Obilct/disposable`; no image was
published to the physical boot partition and no physical reboot was issued.

The actual QEMU test in `build/emulator/recovery-bootfs-guest-20260925/` creates
a new 128-MiB SD fixture with a real 64-MiB FAT32 boot partition. The probe
requires an emulator-marked RAM boot, exact boot UUID/nonce and card identity
before effects. It mounts that fixture partition read-only with private masks
and `nosuid,nodev,noexec`, verifies the kernel mount source/options, requires
EROFS on a write attempt, unmounts and revalidates RAM-only identity. The
whole-card backup then matches the independent fixture hash, and subsequent
modeled watchdog expiry passes after 15.710 seconds with no forced cleanup or
fixture changes. Four probe tests cover bindings, physical-session rejection
and real FAT layout; all 169 recovery tests pass. This qualifies read-only
emulated boot-filesystem access, not boot writes, persistent hold release,
physical restore or a write-capable recovery lease.

Disposable boot-file write/restore qualification now passes in
`build/emulator/recovery-bootfile-roundtrip-20260925/`. An explicit emulator-only
mode mounts the synthetic boot partition with private masks and runs the real
`forge_target_files` primitives: linkless create, metadata readback, idempotent
retry, content replacement, restoration of original content, collision
preservation and restoration to absence. The emitted worker source is retained.
The mount is removed and RAM-only identity rechecked afterward. No physical
boot or root partition is accessed by this test mode.

Independent host hashes verify that only the boot partition changed: the
partition table and entire preceding region, plus the whole root partition,
retain their original hashes. File restoration does not pretend to restore
FAT's deleted-directory/free-cluster bytes; the whole-card hash deliberately
changes. A subsequent 134,217,728-byte backup matches that post-test card's
SHA256 `d615d1d7115e66cc0adbea01a772332aeb0564c5bd6e25f643d3331fa0e0c426`.
After modeled watchdog expiry (15.753 seconds, no forced cleanup), the card
still matches that frozen post-test state. Three new range tests reject root,
partition-table, geometry and late boot mutations; four probe tests pass.
Native macOS passes 13 related tests with one explicit FAT-tool skip in
`build/emulator/macos-bootfile-roundtrip-20260925.log`. This is successful-file-
transaction coverage, not interruption, persistent-hold release or physical
restoration qualification.
The refreshed Linux suite passes all 1,039 tests in 106.279 seconds
(`build/emulator/full-tests-bootfile-roundtrip-20260925.log`). Invalid FAT-mask
types were also checked separately for rejection before filesystem access.

Guarded boot-transition prerequisites (2026-09-25): `forge_recovery_hash` reads
the offline card through a read-only exclusive descriptor, rechecks RAM boot
and native geometry, and records hashes for the root, protected prefix/suffix
and entire card. Its bounded progress stream renews the backup lease without
retaining another large image. Result records explicitly state that this is
not a backup and grants neither root-write nor normal-boot-release authority.
Incomplete transfers retain failed evidence. Five focused tests and all ten
backup-transfer tests pass with the separate hash receipt namespace.

The actual `recovery-storage-hash-guest-20260925` QEMU run verifies every digest
against independently read host ranges after the disposable FAT-file roundtrip.
The 134,217,728-byte post-test card hash is
`c7292af818eb3d001c8c90677c4ddf2e1e460e0d5436d908e1ed1f77c4c29304`;
root and pre-boot regions remain unchanged. Modeled watchdog expiry passes
after 15.643 seconds, without forced cleanup.

`forge_recovery_bootplan` now freezes a single-selector hold/install or release
review, exact native boot-file preimages, checksum-session identity, protected
ranges, an independently supplied root guard and an immutable staging token.
Only `config.txt` content may change. A different current root cannot be
silently adopted for release; source hold/lease/release gates remain inherited.
These plans are rejected by the ordinary file dispatcher and still authorize
no deployment. Five plan tests pass. Twenty related native macOS tests pass in
`build/emulator/macos-boot-guards-20260925.log`. The guarded RAM executor,
interruption handling and physical persistent-hold/release qualification remain
required before root restoration can be enabled.
The Linux regression run passes all 1,049 tests in 90.683 seconds
(`build/emulator/full-tests-boot-guards-20260925.log`); the subsequently added
immutable staging-token check also passes in the five focused plan tests.

The guarded executor now has a separate read-only root-claim primitive in
`forge_recovery_claim`. It holds the root partition with Linux `O_EXCL` while
leaving the separate boot partition mountable, verifies block identity and
length, and hashes root bytes through the held partition descriptor. Prefix
and suffix guards use a read-only whole-card descriptor. Layout checks bracket
acquisition and successful release; exceptions close both descriptors. This is
cooperating-writer exclusion, not protection against privileged raw writers
that ignore kernel claims. Runtime RAM identity and Forge writer fencing remain
the executor's responsibility. Eight focused tests pass on Linux and native
macOS. A separate test rejects physical sessions before the emulator-only
claim probe opens devices or invokes its disposable boot-file worker.

The emulator FAT write-roundtrip validator now holds this root claim throughout
its mount/write/restore/unmount sequence, rejects a competing exclusive root
open, and compares independently supplied root hashes before and after. The
first run in `build/emulator/recovery-root-claim-20260925` hit the old 15-second
command limit during hashing and is retained as failed evidence. The hashing
variant now has a 120-second bound below the initial recovery lease, while
ordinary guest commands retain their 15-second bound. This primitive and
disposable test do not yet implement or authorize the physical CONFIG commit.
The corrected run in `build/emulator/recovery-root-claim-r2-20260925` passes:
the kernel rejects a competing root claim, all seven FAT file checks pass while
the claim remains held, boot unmounts, and the 62,914,560-byte root retains its
independently checked hash
`c9679b1bfe366f7679a662ea63ea1ab8a16002884ad96ebeb45967c462f1e320`.
Only disposable boot-filesystem bytes change. Lease loss causes the expected
software reboot after 298.893 seconds without renewal or forced cleanup; this
is not hardware-watchdog or physical recovery qualification. The final Linux
suite passes 1,058 tests in 90.119 seconds
(`build/emulator/full-tests-root-claim-final-20260925.log`), and nine focused
native macOS tests pass (`build/emulator/macos-root-claim-20260925.log`).

`forge_recovery_bootcommit` implements the internal guarded CONFIG executor:
it checks the exact plan pin and single-file content-only scope, verifies RAM
identity before creating the private transaction lock, holds the root claim
while checking all protected digests, then requires separate owner approval and
90 seconds of independently verified lease/watchdog budget. A dedicated-worker
60-second alarm bounds the mounted phase. The executor verifies all nine boot
preimages and the exact private recovery image, changes only CONFIG, verifies
the desired boot-file state, unmounts boot, and rechecks root/suffix before
returning a receipt. Exceptions after approval mean uncertain completion, never
automatic rollback. A stale-plan retry against an already changed selector is
rejected for reconciliation. No IDE/MCP physical dispatch route is exposed yet;
qualification gates and independent liveness callbacks
must not be replaced with client-supplied success flags.

Fourteen executor tests cover actual local file transitions, dependency/image
conflicts, symlink rejection, ownership and mount options, approval sequencing,
alarm handling, failed unmount, and changed post-write root. Three disposable
CONFIG-probe tests cover compiler-derived inverse plans, independently selected
root guards and physical-session rejection before device access. The Linux
suite passes all 1,075 tests in 90.074 seconds
(`build/emulator/full-tests-config-commit-20260925.log`). Native macOS runs all
17 focused tests with the three Linux target-filesystem tests skipped
(`build/emulator/macos-config-commit-final-20260925.log`).

The new `--boot-commit-roundtrip` watchdog-validator option uses only a freshly
generated synthetic FAT card and nonbootable fixture firmware/image contents.
It seeds compiler-derived staged boot files, independently hashes the card on
the host before each install/release plan, and invokes the actual CONFIG
executor with a live emulated lease/watchdog. Owner source and payloads travel
on stdin, not process arguments. The initial run under
`build/emulator/recovery-config-commit-20260925` exposed the recovery image's
missing `/run/lock` directory before any CONFIG commit; failure evidence is
retained. The executor now creates and validates that RAM directory only after
the bound session/layout check. Physical persistent-hold/release, interrupted
commit reconciliation, and owner qualification-gate integration remain required.
The corrected run `build/emulator/recovery-config-commit-r2-20260925` passes
both actual executor transitions. The install plan pin is
`f4b808ece5daeb708f4270503f9d15675330f57570ce1e630d41f1081574a45c`;
the release pin is
`a26a585c3532df1f8f1f9e4d17fd14b847c536d4560184815f0599c77e9e62dc`.
Both receipts confirm CONFIG application, verified unmount, no root write and
no reboot by the executor. Independent host verification preserves the original
root and pre-boot hashes. The post-test card hash is
`c50fe26f0076c3ceed30935648f41408ae64bb4ea537c9084acc9de8a6c9e751`.
The subsequent modeled watchdog-loss test exits after 15.717 seconds without
forced cleanup. Because QEMU bypasses native firmware selection, this proves
the guarded file transaction, not a physical persistent recovery boot.

The separate host commit channel in `forge_recovery_commit_transport` now
streams owner code and the pinned plan on stdin, with a strict bounded protocol
in `forge_recovery_commit_protocol`. The guest reports completed protected-range
checks and waits up to 60 seconds for a matching attempt/pin/boot approval. The
host requires its owner authorization callback, renews the bound RAM-only
lease, and fsyncs commit intent before sending the acknowledgement. Renewal is
disabled until the guest reports verified boot unmount. Local commit budget
checks independently match the accepted host renewal to private lease state,
the running lease options, and an active 15-second watchdog held by the expected
process. No read-only lease grants write permission. Success additionally needs
post-unmount root/suffix hashes, an exact completion receipt, clean transport
exit and a final RAM-session probe. Each pinned journal permits one attempt;
errors retain uncertain evidence and cannot trigger an automatic retry.

`build/emulator/recovery-commit-transport-20260925` qualifies both install and
release through that real pinned SSH channel. Durable plan pins are
`4b1f2eea8d43634b370513e2c40b789a221f1b066dbe8010059fcba91e04a3b4`
and `864018f09c693e6f927550afeaa9d13135a8f3d37607fbb8e46aaf1e9ccbd4c3`.
Both attempt journals retain dispatch, prepared reply, commit intent, unmount,
protocol transcript, diagnostics and acknowledged completion. Independent
inspection confirms matching journal/receipt pins and ordered prepared,
unmounted and complete messages. The root remains unchanged; the final card
hash is `fb81edcb8609b71719f0549600cc006f609bea310ff2e537e34970ffe1dc20dd`.
Modeled watchdog loss exits after 15.712 seconds without forced cleanup.
The Linux suite passes all 1,090 tests in 91.104 seconds
(`build/emulator/full-tests-commit-transport-20260925.log`). Native macOS passes
the 29-test focused run with three Linux-only skips
(`build/emulator/macos-commit-transport-20260925.log`). Tests include actual
duplex subprocess I/O, lost reply, timeout/process cleanup, strict protocol
order/types, refused approval, durable intent-before-send and retry refusal.
Physical hold/release remains unqualified; this internal channel is not yet
exposed through controller/MCP. Same-boot interruption qualification follows.

Fenced commit protocol version 2 now wraps each guest execution in a private
RAM ledger scoped to the bound boot UUID and plan hash. An executing worker
holds the ledger lock across intent, effects and durable receipt. A fence
rejects a not-yet-started attempt; incomplete attempts cannot run again, and
completed attempts return their stored receipt without repeating effects.
Reconciliation refuses legacy host journals that do not identify this fenced
worker protocol. Existing separate-process ledger tests prove delayed-worker
refusal and that a live effect cannot be fenced.

`forge_recovery_commit_reconcile` retains the original uncertain host record and
writes a new evidence directory. Guest cleanup first verifies the RAM session,
allowing only the exact separately checked stale boot mount for that plan. It
must fence the attempt before acquiring the target lock and root claim or
unmounting that stale mount. Unmount can flush the interrupted worker's pending
FAT writes, which is explicitly recorded; it does not repair or delete files.
Inspection then mounts boot read-only, classifies all nine guarded files, checks
the private image and reports staging conflicts without removing them. A
120-second inspection bound requires at least 180 seconds of verified lease
budget. Prefix hashes must agree before/after that read-only mount. A separate
whole-card hash pass must match both the observed prefix and the independently
approved root/suffix guards. Observation grants neither deployment nor normal
boot-release authority. The initial qualification below is same-boot; explicit
cross-boot and inspector-interruption qualification follow below.

The real SSH emulator run `build/emulator/recovery-commit-reconcile-20260925`
passes two deliberately interrupted transitions:

- Install plan `adcfa5c7e56daf5f0cd7d57d47d6ac2756659de520c8fa3ad20f618cb8e5cd74`:
  SIGKILL after the actual CONFIG write leaves an incomplete guest intent and a
  mounted boot filesystem. Fencing and exact stale-mount cleanup succeed;
  read-only inspection and independent hashes establish `reconciled-after`.
- Release plan `54d5cd691ab90f2cf1587fe677e80dca51fab76280b5e9ee9cd2b503114fefca`:
  the host deliberately loses completion after the guest has durably recorded
  its receipt. Fencing reports completed, no stale mount exists, and observation
  again establishes `reconciled-after` without replaying the write.

Both original host attempts remain `uncertain`; later observations do not
fabricate successful original acknowledgements. Both independent root hashes
remain `c9679b1bfe366f7679a662ea63ea1ab8a16002884ad96ebeb45967c462f1e320`.
The final card hash is
`8adc5678baec066134a6060fd32ee045710d3349410a83860e70119051bcb03e`;
modeled watchdog loss exits after 15.697 seconds with no forced cleanup.
The Linux suite passes 1,099 tests in 90.932 seconds
(`build/emulator/full-tests-commit-reconcile-20260925.log`). Native macOS passes
the 37-test focused run with five Linux-only skips
(`build/emulator/macos-commit-reconcile-20260925.log`).

Reboot-aware reconciliation now requires an explicit observed boot UUID and a
lease bound to that new session. It creates only an observation view: the
original plan, pin and dispatch binding are unchanged, and the view cannot be
executed under the old pin. All other identity/layout/image/file/root guards
remain pinned. After verifying the new RAM session, cleanup reports
`previous-boot-ended` rather than inventing a receipt in the new RAM ledger.
It cannot adopt a stale mount from an earlier boot. Same-boot cleanup still
requires the original ledger fence. A new lease cannot silently rebind an old
journal. Inspector HUP/TERM handling now unwinds bounded unmount cleanup and
ignores repeated hangups during that cleanup; a unit test covers this path.

`build/emulator/recovery-commit-reboot-20260925` passes the complete real-SSH
reboot qualification. Install plan
`c410eb5051e0d5621fb3bb8a6b0029dc6a55aa125ab932a3d08f402e56c9b420`
commits in boot `697b1c0d-2dc5-4b63-bff5-2df21b705bc8`. The guest performs its
guarded software reboot; its QEMU process exits normally before a new process
boots the same disposable card. The new boot UUID is
`f88d5ce3-c75f-417c-a016-05e6e2ff3791`. Explicitly rebound reconciliation proves
the installed selector and unchanged approved root, then a deliberately
resubmitted old worker fails with `Recovery boot changed` before producing any
protocol progress. Independent hashing proves the entire card unchanged across
that reboot, inspection and rejected replay. A newly pinned release plan
`27ccffcb76e6cf4859c3615e6d31812e8ea721943f36ed20603661be51a4f186`
then commits in the new boot. The final card hash is
`13cb533ade2fdf82dd735bfcc39151028c4d1f60625c360169bf5e2c9bfc53f9`;
the root remains unchanged. Modeled watchdog loss exits after 15.736 seconds
with no forced cleanup. QEMU still bypasses native firmware selection, so this
does not qualify physical persistent boot selection.

The Linux suite passes 1,107 tests in 91.243 seconds
(`build/emulator/full-tests-commit-reboot-20260925.log`). Native macOS passes
the 40-test focused run with five Linux-only skips
(`build/emulator/macos-commit-reboot-20260925.log`). Reboot qualification rejects
physical targets and non-disposable machine commands; it never relaunches a
machine solely because an observation timed out.

Inspector interruption now passes the real-SSH emulator test in
`build/emulator/recovery-inspector-interruption-20260925/`. An emulator-only
hook delivers SIGHUP after the read-only inspector reads the guarded files.
Cleanup unmounts boot storage and preserves the incomplete inspection record;
a separate fresh reconciliation establishes `reconciled-after`. Independent
hashing proves the entire card unchanged across both inspections, and the root
remains unchanged through CONFIG install/release. The final card SHA256 is
`0f8822d8cd58e31ab6c8fffe7ea4dabe6db4f3541dc721f695b7a8e9b2d36146`.
Modeled watchdog loss completes after 15.680 seconds without forced cleanup.
This qualifies handled SIGHUP, not SIGKILL, power loss, or physical hold boot.

`forge_ram_boot_observation` separately checks physical firmware selection in
a verified RAM-only session without depending on `/etc/machine-id`. It requires
the expected boot UUID and strict device-tree cells, distinguishing one-shot
tryboot from normal CONFIG selection; neither observation grants write or
normal-boot-release authority. Physical hold journals are prepared, but not
dispatched, in `build/emulator/physical-persistent-hold-20260925/`. The preparation
retains failed intermediate artifacts and identifies the canonical preimages
and ordered planned state in `prepared.json`. No target writes or reboot were
performed during preparation. A fresh full-card backup in the same RAM session
is still required before installing the hold selector.

The refreshed Linux suite passes all 1,112 tests in 90.724 seconds
(`build/emulator/full-tests-inspector-r2-20260925.log`). Native macOS passes the
22-test focused run with two Linux-only skips
(`build/emulator/macos-inspector-20260925.log`). These checks include rejection
of physical inspector fault injection before any target access.

Independent backup range guards (`forge_recovery_archive_hash`) now stream a
completed private whole-card gzip archive without creating another full-sized
image. They verify the full card hash and bounded length while deriving the
prefix/root/suffix digests, reject source replacement or mutation, and retain
incomplete evidence on failure. A lease heartbeat can keep the same RAM session
alive during host-side verification. These guards confer no filesystem-health,
restore, or normal-boot-release authority.

The retained physical archive passes this check in
`physical-full-card-backup-20260925/independent-range-hashes`, reproducing card
SHA256 `1c958385a34ff97119e1a99b93a94330d6b5595c234dc7ce70d175cd29816fdc`
and root SHA256
`c1ebdf0e7291bc4a63442ab3ad17d145f51ecb9a7d8c407dd2fcfd12a0deb96b`.
This is the faithful original backup, not the separately repaired host copy.
All 1,117 Linux tests pass (91.328 seconds,
`build/emulator/full-tests-archive-hash-20260925.log`); the 14 focused archive
tests also pass natively on macOS (`macos-archive-hash-20260925.log`).

The owner-run physical hold qualification is now dispatched from
`physical-persistent-hold-20260925/run_trial.py`. It requires a fresh whole-card
backup and independently derived archive root guard in the initial RAM boot,
then CONFIG-only hold installation, physical normal-selection RAM reboot,
cross-boot reconciliation, unchanged-root release, normal return and restoration
of all nine staging preimages. It retains uncertain attempts without automatic
retry or rollback. Completion must be established by its final acceptance;
dispatch alone does not qualify physical hold or any root restoration.
That trial's fresh physical backup is now complete: 31,914,983,424 bytes are
retained in a 5,828,462,111-byte compressed archive. Both archive verification
and the independent range pass reproduce card SHA256
`c5ec25df00d7bb7fa5c2d24506343717c550ec7cffc855c065580333cb721eb7` and root
SHA256 `6e0e4ecddb0752dc35db1ab29fdc7ccde2e9bc8d4a726325e71be739aae0a435`.
Evidence is in that trial's `card-backup/acceptance.json` and
`backup-range-hashes/acceptance.json`. The same leased RAM session proceeds to
live precondition hashing; these receipts alone do not qualify hold installation,
filesystem consistency, normal return or root restoration.

The host restore source now has a frozen 4-MiB chunk manifest
(`forge_recovery_restore_source`). Preparation verifies the complete retained
card archive and the independently approved root hash before publishing the
manifest. Streaming rechecks archive identity, verifies each root chunk before
delivery, and requires gzip completion, whole-card/root hashes and stable source
metadata before returning success. A disconnected consumer or changed source is
not successful transmission. Source success is explicitly not target restore
or normal-boot-release evidence.

The full-size host-only qualification in
`physical-full-card-backup-20260925/restore-source/stream-acceptance.json`
passes all 7,481 chunks of the 31,373,918,208-byte original root. Manifest SHA256
is `f8c8dc2e80802a1351c86a19fa5e3961475a5b87381760826294efd805981f3e`;
an independent consuming hash matches the original root hash above. No target
was contacted, and no additional raw image copy was created.

`forge_recovery_restore_chunks` implements only the internal byte-writing
component: it receives an already authorized, exclusively held root-partition
descriptor. It neither opens a device nor supplies that authorization. Pinned
chunks must arrive in order, match their hashes before writes, synchronize and
pass readback before acknowledgement. Short writes are completed; errors make
the writer terminal without automatic retry or rollback. Matching bytes still
require synchronization. Finalization rereads the entire root with progress
reporting, and grants no boot-release authority. Disposable-file tests cover
partial/zero writes, synchronization/readback failures, later corruption and
write bounds. The physical claim, persistent-hold proof, restore transport,
fencing/reconciliation and interrupted hardware restore remain integration and
qualification gates; this internal component is not exposed through GUI/MCP.
The refreshed Linux suite passes 1,137 tests in 92.707 seconds
(`build/emulator/full-tests-restore-chunks-20260925.log`). The 34 focused
source/archive/chunk-writer tests pass natively on macOS
(`build/emulator/macos-restore-chunks-20260925.log`). These host results do not
qualify writing a physical root partition.

Restore framing (`forge_recovery_restore_protocol`) now binds every frame to
the plan digest, source-manifest digest, attempt nonce and boot UUID. Headers
are limited to 4,096 bytes, reject duplicate JSON fields and require exact
ordered chunk descriptors. A complete chunk is buffered and hash-checked before
any device write. Completion requires a distinct verified-source
record and EOF, then full target-root verification; none of these messages
authorizes normal boot. Failed delivery or acknowledgement makes that protocol
instance terminal rather than resending an uncertain write.

Linux and macOS tests exercise actual child-process pipes into disposable host
root files. The successful path returns two ordered chunk acknowledgements and
a verified-root result. Closing the input after the first chunk retains exactly
that partial root and its acknowledgement, exits unsuccessfully, and never emits
root completion. Additional cases cover wrong boot/attempt/order, oversized or
duplicate headers, truncated bodies, missing completion, extra bytes, short
transport writes and acknowledgement failure. This qualifies framing and the
host-file data plane, not live SSH restore transport, write claims, persistent
fencing, physical interruption or recovery-hold release.
The full Linux suite passes 1,148 tests in 93.933 seconds
(`build/emulator/full-tests-restore-protocol-20260925.log`); native macOS passes
the 45-test focused run (`build/emulator/macos-restore-protocol-20260925.log`).

The internal writable claim (`forge_recovery_restore_claim`) now requires a
separate, exact owner restore approval before opening the root partition with
`O_RDWR|O_EXCL`. A backup-lease receipt cannot satisfy that approval. The whole
card descriptor remains read-only. Fresh RAM identity/layout checks bracket
approval, descriptor acquisition, initial hashing, the root operation and final
protected-range hashing. All three ranges must match before the descriptor is
yielded; the prefix (including partition table and boot storage) and suffix must
still match on successful return. Errors close both descriptors without guessing
rollback or claiming restored-root success. As with the read-only claim, this
does not constrain a privileged raw writer that deliberately ignores claims.

`--root-write-claim` adds an explicitly emulator-only small-card qualification
to the recovery validator. Its worker verifies the emulator boot marker and
bound synthetic MMC identity before touching the root. It rejects competing
read-only and writable exclusive claims, deliberately changes one root sector,
reads it back, explicitly restores the retained sector and verifies the root
and protected card ranges. A separate host hash checks the entire final card.
The write probe rejects physical mode, arbitrary device paths and oversized
cards. This is a writable-claim qualification, not the persistent-hold/restore
executor or an interrupted physical restore.

The real-SSH emulator run in
`build/emulator/recovery-root-write-claim-r2-20260925/` passes the writable-claim
roundtrip and both competing-claim rejections. The entire final card retains
SHA256 `948cc30d738f0f1e7acabf0ce59bb01277796aa3c5cc59130394864d451752d9`.
Its evidence records `root_written=true`, `root_restored=true` and
`probe_read_only=false`; unchanged final bytes do not imply that no writes
occurred. Modeled watchdog loss exits after 15.662 seconds without forced
cleanup. The first run's byte/claim checks passed too, but its inherited
read-only summary label was incorrect; that record is retained, and the r2
run qualifies the corrected reporting.
The full Linux suite passes 1,158 tests in 92.735 seconds
(`build/emulator/full-tests-root-write-claim-r2-20260925.log`); the 24 focused
claim/probe tests pass natively on macOS
(`build/emulator/macos-root-write-claim-r2-20260925.log`).

The physical backup-root restore planner (`forge_recovery_restore_plan`) now
binds the saved chunk manifest and backup plan to the current hardware serial,
SD CID, DOS disk ID, kernel, device and complete root extent. It requires a
newer verified physical RAM boot using normal firmware selection, the same
recovery lease owner, intact held files/image, and current read-only range
hashes whose prefix matches the hold inspection. Emulator evidence, one-shot
tryboot, mounted storage, another card's backup and mismatched source geometry
are rejected. The desired root comes from the frozen backup, never from silently
adopting the current root hash.

Preparation retains private input evidence and a pinned plan; loading recompiles
those inputs under the external owner's plan digest. The only planned writable
device is the root partition. All write/release authority flags remain false,
and filesystem-health review, prior-writer fencing, live hold/lease checks,
protected-range verification and a separate native-boot release approval remain
explicit executor gates. No root-restore dispatch or GUI/MCP route is added by
this planner. Its tests cover target/boot/source mismatches, source geometry,
hold conflicts, private exclusive journals and changed retained evidence.
The full Linux suite passes 1,167 tests in 92.952 seconds
(`build/emulator/full-tests-restore-plan-20260925.log`), and 27 focused
source/claim/planner tests pass natively on macOS
(`build/emulator/macos-restore-plan-20260925.log`).
The fresh physical backup's host-only source preparation also passes in
`physical-persistent-hold-20260925/future-restore-source/acceptance.json`: 7,481
chunks, manifest SHA256
`665052091d07ca5b20cb01864dda76844b5676b3597108f359b46179df2b758a`.
No target was contacted by that preparation and no restore was dispatched.
The ongoing hold trial must still supply its physical normal-selection boot
and held-file evidence before a physical restore plan can be compiled from it.

Root restoration now has a separate boot-wide RAM ledger
(`forge_recovery_restore_ledger`). All plans share the canonical directory for
that boot, so an incomplete attempt blocks new writes even under another plan
digest. A pre-start fence rejects delayed workers; the ledger lock covers intent,
effect and receipt, and a running effect cannot be fenced. The exact boot UUID
is checked before access and around effects. Receipt validation pins the source,
root, protected ranges and byte count and cannot grant normal-boot release.
Inputs are frozen before callbacks can mutate them.

An incomplete root attempt retains its intent and reports that a newly verified
recovery boot is required before another plan can execute. There is no fabricated
completion, deleted intent, implicit rollback or automatic reboot. The persistent
hold and independent new-boot observation must support that recovery policy.
Completed receipt replay is historical evidence, not a fresh observation that
the target still contains those bytes; live readback and release checks remain
separate. Tests include a delayed independent process fenced before it can act,
cross-plan exclusion, active-worker exclusion, stale-boot rejection, mismatched
receipts and rejection of alternate ledger directories.
The refreshed full Linux suite passes 1,176 tests in 92.835 seconds
(`build/emulator/full-tests-restore-ledger-r2-20260925.log`). Native macOS passes
27 focused ledger/planner tests, including the separate-process fence
(`build/emulator/macos-restore-ledger-r2-20260925.log`). This establishes the
ledger primitives, not end-to-end interrupted physical restoration.

The executor's fresh physical identity guard is now implemented in
`forge_recovery_restore_identity`. It freezes the reviewed binding and brackets
each actual MMC layout observation with independent physical RAM/firmware
observations. Both must report the pinned boot UUID, normal firmware selection,
RAM-only mounts and exact recovery lease owner. A changed card, partition table,
boot, owner, mounted filesystem or emulator marker fails closed. The guard is
read-only; it neither grants restoration authority nor replaces held-file,
watchdog, transport or exclusive-claim checks. Seven new tests cover those
boundaries, observation ordering and binding/result mutation. The 25 focused
identity/planner/ledger tests pass on Linux and native macOS
(`build/emulator/macos-restore-identity-20260925.log`). Integration into the
complete root executor and physical restoration remain outstanding.
The refreshed full Linux suite passes all 1,183 tests in 92.873 seconds
(`build/emulator/full-tests-restore-identity-20260925.log`). The existing physical
hold trial has recorded the post-commit unmount and entered final root readback;
that intermediate event is not yet a completed hold-installation receipt.

The internal physical root executor (`forge_recovery_restore_executor`) now
recompiles retained evidence under the owner pin, checks fresh physical identity,
holds the shared Forge target lock and boot-wide restore ledger, then acquires
the exclusive writable root claim. Separate owner exchanges precede acquisition,
read-only held-file/image inspection and root streaming. Inspection has a
120-second mount limit and requires at least 180 seconds of verified lease;
streaming requires fresh identity and lease checks before each chunk. The
inspection unmounts before renewal resumes, and the protected prefix is rehashed
before any root chunk is written. Final completion is journaled only after full
root readback and successful protected-range checks on claim exit.

Eleven executor tests use real disposable file bytes, range hashes, framing,
chunk writes and durable ledgers, with hardware identity, mounts and block
claims mocked. They cover retained-evidence changes, conflicting hold files,
missing write approval, lease expiry, partial streams, protected-prefix
corruption, callback mutation and historical receipt replay without repeating
effects. The 27 focused executor/identity/ledger tests pass on Linux and native
macOS (`build/emulator/macos-restore-executor-20260925.log`). This is not physical
restore qualification. The executor still has no public dispatch route; its
installed-owner worker must supply deadline-bounded input, acknowledged lease
renewals and explicit source-health review before physical use. Duplex SSH
transport, interruption reconciliation and physical restore testing remain open.
The full Linux regression suite also passes all 1,194 tests in 94.088 seconds
(`build/emulator/full-tests-restore-executor-20260925.log`).

`forge_recovery_restore_io.PipeIO` adds nonblocking, deadline-bounded binary
input/output for the dedicated restore worker. It preserves prefetched binary
body bytes across header parsing, limits reads and buffered data, and applies
both a fixed total deadline and per-operation progress deadlines. Timeout,
broken output or invalid I/O bounds make the adapter terminal rather than
permitting a retry. Input EOF remains distinct from output completion so the
worker can report the final verified receipt after the source closes its side.
It never renews a lease or authorizes device access.

Fourteen tests cover partial headers, stalled bodies/EOF, blocked and broken
output, bounded headers, invalid descriptors/deadlines and descriptor cleanup.
Two run the real chunk receiver in separate processes over actual pipes: the
complete stream restores the disposable root exactly, while disconnection after
one chunk retains partial bytes and produces no root-completion receipt. They
also distinguish time spent hashing locally from time waiting for peer progress:
each I/O operation gets an idle budget, but none extends the total deadline.
All 25
focused pipe/executor tests pass on Linux and native macOS
(`build/emulator/macos-restore-io-r2-20260925.log`). The bounded adapter still needs
the owner-worker handshake and host duplex transport; it is not a physical
restore dispatch route.
The full Linux run passes 1,207 tests in 95.307 seconds
(`build/emulator/full-tests-restore-io-20260925.log`); the additional idle-budget
test was added after that run started and passes in the 25-test focused rerun.

The restore worker control hooks (`forge_recovery_restore_worker`) now serialize
explicit owner approvals and lease renewals on the same bounded control channel.
Every challenge binds the plan, manifest, attempt, boot, phase and increasing
control sequence; acknowledged lease receipts must also advance and match the
actual private local lease and live watchdog. Renewal is suspended across boot
inspection until verified unmount. Long verification hashes can request renewal
through their progress callbacks. The host must send one chunk at a time and
service control challenges while awaiting that chunk's acknowledgement.

Source completion no longer requires closing the control channel prematurely:
the installed executor may defer EOF through root readback and protected-range
verification, then request an input half-close. It still rejects trailing bytes
and requires actual EOF before journaling completion. The default framing API
retains its original immediate-EOF behavior. Historical ledger replay is marked
explicitly by the worker and is not fresh root evidence. Ten worker tests and
three additional executor tests cover bound/replayed control replies, local
liveness rejection, mount-phase renewal exclusion, long-readback renewal,
deferred-EOF rejection and failure without a durable receipt. All 49 focused
worker/executor/framing/pipe tests pass on Linux and native macOS
(`build/emulator/macos-restore-worker-20260925.log`). The owner request bootstrap,
host duplex transport and physical root-restore qualification remain pending.
The full Linux suite passes all 1,221 tests in 95.169 seconds
(`build/emulator/full-tests-restore-worker-20260925.log`).

The host-side restore protocol checker (`forge_recovery_restore_host_protocol`)
now freezes the pinned plan/source and checks the worker's entire fresh-attempt
sequence. Acquisition, inspection, verified unmount and write approval must occur
in order; renewal is forbidden during the mounted phase. Each sent chunk must be
acknowledged against its exact manifest entry, synchronized status and byte count
before another chunk is sent. Source completion, final readback progress and the
requested input half-close must all precede an exact final receipt whose written
byte count matches the chunk receipts. Historical replay cannot satisfy a fresh
attempt. Any protocol violation makes the checker terminal.

Fourteen new tests include actual worker control hooks exchanging messages with
the host checker, along with wrong attempts, skipped approvals, premature
completion, invalid/foreign lease evidence, changed source digests, invalid write
sizes and immutable-input checks. All 24 host-protocol/worker tests pass on Linux
and native macOS (`build/emulator/macos-restore-host-protocol-20260925.log`). This
checker performs no I/O or owner authorization: bounded process/SSH exchange and
durable host dispatch/reconciliation still need integration before physical use.
The full Linux suite passes all 1,235 tests in 95.185 seconds
(`build/emulator/full-tests-restore-host-protocol-20260925.log`).

The duplex exchange core (`forge_recovery_restore_exchange`) now connects the
host checker, verified archive producer and worker framing. It records owner
approval and control-response intent before delivering approval, then sends one
verified chunk at a time and services renewal challenges while awaiting its
acknowledgement. Source EOF/CRC verification precedes source completion; root
readback and input half-close precede final worker output. Unexpected trailing
output or nonzero child/SSH exit prevents host completion even when a plausible
root receipt was received. Callers must provide authenticated process setup,
deadline-bounded I/O, durable journal callbacks and explicit source-health review;
there is still no physical dispatch route.

Eight new exchange tests run a separate child over actual socket/pipe endpoints
with the real worker hooks, chunk receiver/writer and host checker. The child
uses synthetic liveness and protected-range receipts, so these are transport
tests, not hardware qualification. Cases include success with renewals between
chunks and during readback, denied approval, journal failure before approval
delivery, lost chunk acknowledgement, trailing output, nonzero exit, unverified
source bytes and archive failure after the last chunk. All 32 focused
exchange/host-protocol/worker tests pass on Linux and native macOS
(`build/emulator/macos-restore-exchange-20260925.log`).
The full Linux suite passes all 1,243 tests in 96.338 seconds
(`build/emulator/full-tests-restore-exchange-20260925.log`).

The process wrapper (`forge_recovery_restore_process`) now owns the local
worker/SSH pipes through bounded I/O, input half-close, output EOF and exit-status
verification. It drains diagnostics alongside protocol reads and writes, without
letting diagnostic activity extend protocol progress deadlines. Diagnostics are
capped at 64 KiB and protocol output at 64 MiB. Failure terminates and reaps the
owned child without retrying; a diagnostic flush failure cannot hide an existing
operation error. The caller still owns authenticated argv construction, private
durable journals and target authorization.

Eleven process tests cover half-close, diagnostics larger than a pipe buffer,
output bounds, nonzero exit, stalled protocol, a process that closes output but
does not exit, callback failure and cleanup. The eight existing duplex tests now
use this process wrapper rather than test-only launch/cleanup code. All 19 focused
process/exchange tests pass on Linux and native macOS
(`build/emulator/macos-restore-process-20260925.log`). Physical restore dispatch
and interruption reconciliation remain unqualified.
The full Linux suite passes all 1,254 tests in 97.992 seconds
(`build/emulator/full-tests-restore-process-20260925.log`).

The owner bootstrap (`forge_recovery_restore_bootstrap`) now freezes the worker's
installed local import closure (currently 37 modules, 247,570 source bytes) with
the recompiled pinned plan and retained inputs. A fixed physical-only recovery
SSH command pins the entire packet SHA256 separately from stdin. The remote
bootstrap reads an exact bounded length under a deadline, checks that pin before
executing owner code, rejects duplicate/nonfinite JSON and invalid module names,
and uses an in-memory loader with no fallback to guest-installed Forge modules.
It writes no code files to target storage. A pinned readiness response establishes
only code loading; physical identity, hold and write authorization remain later
mandatory worker gates.

Ten tests cover malformed/oversized/truncated packets, modified pins, missing
dependencies, isolated imports of the real worker closure, preservation of bytes
after the bootstrap frame, and pinned SSH options/credential changes. Integration
with the bounded process wrapper loads the packet and continues over the same
channel. All 21 bootstrap/process tests pass on Linux and native macOS
(`build/emulator/macos-restore-bootstrap-20260925.log`). This does not yet dispatch
a physical restore: durable host attempt journaling and reconciliation remain
required before that route is enabled.
The full Linux suite passes all 1,264 tests in 99.373 seconds
(`build/emulator/full-tests-restore-bootstrap-20260925.log`).

The internal dispatch path (`forge_recovery_restore_dispatch`) now joins pinned
bootstrap, process ownership, duplex exchange and archive streaming under an
exclusive private plan-journal lock. It rechecks source identity, physical normal
RAM selection, exact layout and credential pins before creating one exclusive
`restore-attempt` directory. The retained dispatch binds plan, attempt, boot,
packet, source manifest and source-health digest. Events are bounded, sequenced
and fsynced before the corresponding owner approvals or chunk intents can reach
the target. A prior attempt blocks redispatch before any further target access.

The pinned filesystem-check receipt must describe the exact source card. A
non-clean source requires the installed owner callback to explicitly acknowledge
its filesystem errors at each approval phase; it cannot be treated as a clean
image merely because its backup bytes are verified. Transport failure or a changed
boot after completion retains `uncertain` with root-write state unknown. No retry,
rollback, release authority or physical-restore qualification is inferred.

Ten dispatch tests use real private journals and verified archive bytes, with
target/protocol execution mocked. They cover durable health approval, prior
attempt rejection without target contact, uncertain transport, changed source,
wrong layout/boot, post-completion reboot, failed fsync and bounded events. All 28
dispatch/bootstrap/exchange tests pass on Linux and native macOS
(`build/emulator/macos-restore-dispatch-20260925.log`). The dispatch route remains
internal and has not been used for physical root writes; same/new-boot uncertain
restore reconciliation and end-to-end hardware qualification are still pending.
The full Linux suite passes all 1,274 tests in 99.090 seconds
(`build/emulator/full-tests-restore-dispatch-20260925.log`).

The restore observer (`forge_recovery_restore_observe`) now fences the original
same-boot attempt before cleanup; an active writer prevents fencing. A newly
verified boot records `previous-boot-ended` without creating a replacement for
the old RAM ledger. Cleanup accepts only this attempt's exact private read-only
boot mount, or its empty private RAM mountpoint. Writable/unrelated mounts,
nonempty directories and unsafe metadata fail closed. The subsequent inspection
holds a read-only root claim, checks held boot files/image under a bounded
read-only mount, and requires identical prefix hashes before and after inspection.

The reconciliation classifier (`forge_recovery_restore_reconcile`) validates the
fence, held-file inspection and independent whole-card hash bindings, then reports
source-matched, before, partial-or-diverged or conflict. A completed receipt must
agree with freshly observed root bytes. Source-matched bytes without a completed
receipt never fabricate historical completion. Same-boot incomplete attempts
still require a new recovery boot before another write; no classification grants
restore or boot-release authority.

Nineteen observer/classifier tests cover mounted-state restrictions, active-writer
exclusion, new-boot ledger boundaries, stale RAM cleanup, prefix/budget failures,
partial roots, receipt/data conflicts and foreign evidence. Together with the
existing ledger tests, all 28 focused tests pass on Linux and native macOS
(`build/emulator/macos-restore-observation-20260925.log`). Hardware boundaries are
mocked in observer tests; host transport/journal integration and interrupted
physical-restore qualification are still pending.
The full Linux suite passes all 1,293 tests in 99.334 seconds
(`build/emulator/full-tests-restore-observation-20260925.log`).

The restore observer now has a separate pinned bootstrap entry point and a
bounded host process exchange (`forge_recovery_restore_observe_worker` and
`forge_recovery_restore_observe_transport`). Its exact request accepts only
fence or inspect, recompiles retained plan evidence, binds the original attempt,
query, observed boot and lease owner, and requires input EOF before target
operations. Inspection receives an explicit accepted lease; fencing receives
none, allowing stale read-only mount cleanup before renewal. Responses require
the matching query binding, output EOF and successful process exit. This route
does not invoke the restore executor or authorize writes or hold release.

Nine new tests cover malformed requests, changed evidence, trailing input,
new-boot observation, isolated pinned imports, reply binding, and an actual
subprocess exchange with only the hardware boundary replaced by a fixture.
All 38 observer/bootstrap/classifier tests pass on native macOS in
`build/emulator/macos-restore-observer-transport-20260925.log`.
The Linux suite passes 1,301 tests in 99.954 seconds in
`build/emulator/full-tests-restore-observer-transport-20260925.log`; the additional
subprocess test was added after that discovery started and passes separately
with all nine new worker/transport tests on Linux as well as in the macOS run.
These tests do not qualify physical root restoration or interrupted-restore
recovery.

Host restore reconciliation (`forge_recovery_restore_reconcile_host`) now
connects that transport to an exclusive private query journal. It validates the
retained original dispatch/source-health pins and recompiles the original plan,
holds the same host lock as dispatch, and preserves the original attempt bytes.
The old worker packet pin remains historical provenance, rather than being
silently replaced with a packet rebuilt from newer installed code. New boot
observation requires an explicit UUID and that boot's bound lease.

Each request is durable before SSH. The new packet pin, parsed reply and process
exit evidence are retained independently, including a reply received before a
later transport failure. Fencing must validate before lease renewal; inspection
must validate before an independent full-card hash. Hash receipts are checked
against their retained journal and exact observed boot/layout before strict
classification. A final RAM identity check precedes acceptance. Failures retain
incomplete evidence, never retry writes or authorize normal boot release.

Twelve new host tests cover ordering, immutable original evidence, independent
repeat queries, wrong pins, lost transport, malformed receipts, new boots,
protected-range conflicts, mismatched retained hashes, final boot changes,
competing locks and journal failures. All 40 focused host/observer/classifier/
dispatch tests pass on native macOS in
`build/emulator/macos-restore-reconcile-host-final-20260925.log`. The Linux suite
passes 1,314 tests in 132.730 seconds in
`build/emulator/full-tests-restore-reconcile-host-20260925.log`, with the 21
host/observer tests additionally rerun after adding packet/reply retention.
Target boundaries are mocked in the host tests; end-to-end disposable-card
restore/interruption and physical root restoration remain pending.

The disposable recovery validator now has `--root-restore-stream`, gated on its
synthetic CONFIG transport/hold roundtrip. `forge_restore_stream_probe` backs up
the actual emulated card, prepares a verified chunk source, makes one explicit
sector change under an exclusive writable root claim, and restores that backup
through the production bootstrap, worker, owner/lease exchange, chunk writer,
full-root readback and durable RAM ledger. Independent full-card hashes must
match the pre-change image afterward; matching source chunks must be skipped.
The test-only worker substitutes physical plan compilation and identity
selection with a fresh emulated RAM/MMC check. Production physical validation
is unchanged; the fixture rejects physical mode, other devices and cards above
128 MiB and never claims physical qualification. In
`recovery-root-restore-stream-r2-20260925/root-restore-stream/`, the restore
subtest passes with 15 acknowledged chunks: one changed 4,194,304-byte chunk
written and 14 matching chunks skipped. A real lease renewal occurred during
the exchange. Source EOF, full-root readback, protected-range checks, input
half-close and successful worker exit preceded the completion receipt.
Independent whole-card hashing reproduces all 134,217,728 original bytes with
SHA256 `29e7a08f4fc174193c9cac49f241e780fe45f87b161b573457831579fc871166`.
The enclosing trial also passes: both CONFIG transitions are verified, modeled
watchdog exit follows keeper loss in 15.684 seconds, and no forced cleanup was
needed. Its separate top-level `acceptance.json` and terminal zero exit retain
that evidence.
This is stream/executor evidence on a disposable card, not physical identity,
firmware selection, filesystem health, the physical host-dispatch policy or
interrupted-restore qualification. The full Linux suite passes 1,317 tests in
121.239 seconds (`full-tests-restore-stream-20260925.log`), and native macOS
passes 27 focused tests (`macos-restore-stream-20260925.log`), both under
`build/emulator/`.

Lost-completion qualification adds an explicit disposable-only fault after the
restore executor has durably recorded its result but before the final worker
reply. The host must retain an uncertain original outcome, fence the same
attempt and independently hash the card; it never resends restore data. A
historical receipt is retained separately from an acknowledged completion.
The first trial (`recovery-root-restore-lost-completion-20260925/`) failed before
the hold/restore phase: the guest rebooted during lease renewal. Its failed
acceptance and console log remain retained and are not fault-test acceptance.

Investigation reproduced lease-publication races with three deterministic
regressions (`lease-publication-race-before-20260925.log`). A renewal published
after the timer sampled its clock could look like a future/reversed timestamp.
Also, atomic replacement could unlink the reader's already-open immutable
snapshot, changing its link count and ctime without changing its bytes. Both
were treated as fatal. The failed guest's old runtime did not report the reason
before reboot, so these reproductions do not establish which condition caused
that specific reboot.

Readers now validate against a post-read clock sample and allow the exact
unlink transition for an open snapshot. Clock reversal, future publication,
expiry, changed permissions/content and additional hard links remain rejected;
the publisher's named-file checks stay strict. Bounded console reason codes
are emitted before recovery failure/reboot without logging arbitrary exception
text or lease contents. A final guard also compares the third clock sample
against the post-snapshot sample so a reversal between them cannot be hidden.
Forty-two focused Linux tests pass in
`lease-publication-race-final-r2-20260925.log`. macOS runs 47 focused tests, with
12 Linux-only skips, in `macos-lease-publication-race-final-20260925.log`.
The final Linux suite passes all 1,326 tests in 101.232 seconds in
`full-tests-lease-publication-race-final-20260925.log`, including the final
clock-reversal and in-place metadata-change rejection tests.

A new disposable-only native recovery build passed module, Python and SSH
preflight. Its retained image in `recovery-lease-race-native-20260925/` is
46,128,523 bytes, SHA256
`3c30e9ca572fae3c8af874cfe2d7f67e88595e6efd32cde2b232b23991ae3397`.
It has not been published to physical boot. The fresh QEMU fault trial is
`recovery-root-restore-lost-completion-r2-20260925/`. Its restore subtest now
passes: all 15 chunks/readback completed, the final reply was deliberately lost,
and `restore-acceptance.json` remains uncertain with unknown written state.
The original target attempt fenced as completed; its receipt is retained in
`historical-receipt.json`, not relabeled as an acknowledged host completion.
Independent full-card hashes matched the saved source, without a restore retry.
The enclosing hold-release/watchdog trial also passes with both CONFIG
transitions verified, modeled watchdog exit in 15.728 seconds, no forced
cleanup and terminal zero host exit.
The final clock guard is included in the separately retained native rebuild
`recovery-lease-race-native-r2-20260925/`, 46,128,282 bytes, SHA256
`6325f8deb8b1d1ebabf98dbd042bc40b1558a3c21c0ee388464613ea2dde5070`.
It also passed native build/module/Python/SSH preflight. Its independent
340-second renewal soak and watchdog trial is
`recovery-lease-publication-soak-20260925/`, passes with 12 retained samples:
the same boot remained live through guest monotonic 363.525, beyond its original
308.009 deadline. Modeled watchdog exit occurred 16.989 seconds after keeper
loss, with no forced cleanup and terminal zero host exit. Neither new disposable
image has been published to physical boot. The lost-receipt trial uses the first
race-fixed image; the soak uses the final image with the additional clock guard.
Physical recovery must be rebuilt and requalified before physical root writes.

The next disposable qualifier, `--root-restore-interruption`, seeds changes in
two separate source chunks and SIGKILLs the restore worker after its first
chunk's fsync/readback but before acknowledgement. It requires the exact
remaining partial-root digest predicted from the verified backup, unchanged
protected ranges, and an incomplete original target ledger. A separate
same-boot request must be rejected before approval/data; after an explicit new
recovery boot, the old worker must be rejected by its boot binding. Only a
fresh plan/attempt/lease may then finish restoring the original source, followed
by independent whole-card comparison. Original uncertainty and new-boot
completion have separate journals. This remains an emulator-only fixture, not
a public retry path or physical qualification. The completed trial is retained in
`recovery-root-restore-interruption-20260925/` and passes: the exact partial-root
digest and protected ranges matched, both negative requests were rejected, and
the new-boot restore wrote only the remaining 4 MiB chunk of 15 source chunks.
Independent whole-card comparison confirms the original card was restored.
The original host result remains `uncertain`; the fresh-boot completion is a
separate receipt, not a rewrite of the interrupted attempt. The enclosing
hold-release/watchdog trial also passed without forced cleanup and exited zero.
Regression verification passed 1,329 Linux tests and 31 focused native macOS
tests (`full-tests-restore-interruption-20260925.log` and
`macos-restore-interruption-20260925.log`). Physical root restore remains open.

The final lease fixes have now been built into a separate credential-bearing
physical candidate on `clockworkpi.local`, retained under
`/var/tmp/forge-lease-race-build.fA8K4N/physical-private-r2/`. Its image is
46,128,784 bytes, SHA256
`b28c59db775fab23eb35ee68e5c275ceec2f3ee87de1629524c5978e7ae09544`.
Native offline preflight passed: unpacked runtime source and private credentials
match, module dependencies resolve, SSH configuration validates, and Python and
lease imports succeed. Evidence is retained in
`recovery-lease-race-physical-preflight-20260925.log`; the validator is retained
as `validate_private_race_image.py`. This candidate has not been published or
booted. The normal physical boot remains unchanged; a fresh guarded boot trial
is required before it can support physical restore qualification.

That exact private image then passed the emulator qualification in
`recovery-private-race-soak-20260925/`: 340-second sustained renewal beyond the
original deadline, Python/network-observer checks, synthetic storage/FAT checks,
and modeled watchdog expiry in 15.652 seconds, with no forced cleanup and zero
host exit. The physical candidate was published with journal pin
`7f76a8ce276fd2256c0c5d58af6338ab3723f9919d5730bc160803d4ed224e95`;
independent inspection confirmed matching bytes, absent scratch and valid
private boot-mount policy. The fresh one-shot RAM trial is retained in
`physical-private-race-trial-20260925/`, nonce
`ce8fe9a25ed949b8a6a9443d043520a3`. Its runner pins the current normal boot,
publication, four staging phases and earlier failed-root firmware evidence.
It renews for six minutes, then awaits automatic normal return before restoring
all nine boot-file preimages. This trial is running, not yet qualified; it does
not write the root partition or install a persistent recovery hold. The focused
publication/boot-observation regression suite passed 33 tests in
`private-race-publication-tests-20260925.log`.

The physical private-image trial has reached the same-boot sustained-renewal
gate: twelve renewals were acknowledged, and guest monotonic time reached
379.466 versus the original 305.486 deadline. Its boot UUID is
`1eef37c2-fca2-451c-a944-e0763fb64bdc`. Renewals have stopped; normal return and
nine-preimage restoration are still pending. This is not whole-trial acceptance.

Source materialization and offline filesystem checking now accept an optional
caller-owned heartbeat for long physical recovery sessions. Extraction/copy
chunks and checker polling maintain that lease; even a checker that closes its
output but remains alive is polled under the original deadline. A renewal
failure kills/reaps the owned checker and retains incomplete evidence, never
restore authority. Verification passed 1,334 full Linux tests, followed by
22 focused Linux tests including two additional failure/deadline cases. Evidence
is in `full-tests-source-health-heartbeat-20260925.log` and
`source-health-heartbeat-tests-r2-20260925.log`. The prepared native unchanged-
source stream trial is `physical-restore-stream-20260925/`; it has not been
dispatched. It requires this private-image trial to finish successfully, then
a fresh offline backup and separate persistent RAM hold before streaming.
An unchanged-source result will not qualify actual root writes or enhanced
image deployment. Three redundant earlier source-health image copies were
removed under the user's cleanup authorization; the original compressed backup
and all checks/logs remain, with exact paths in
`physical-persistent-hold-20260925/source-health-copy-cleanup.json`.
The final heartbeat revision subsequently passed all 1,336 Linux tests in
105.389 seconds (`full-tests-source-health-heartbeat-r2-20260925.log`) and
22 focused native macOS tests with nine Linux-only skips
(`macos-source-health-heartbeat-tests-r2-20260925.log`).

The private-image physical trial subsequently completed with sustained lease,
automatic normal return and all nine boot-file preimages verified, no renewal
failure and no root writes. Normal boot UUID is
`7e9198cb-2417-4428-a70b-7e537312112a`; the runner exited zero. The subsequent
unchanged-source native stream trial has now been prepared and dispatched under
fresh nonce `2b768122091c433084ec78f1641908ec`, with journals/logs in
`physical-restore-stream-20260925/`. Its fresh backup, health checks, persistent
hold, stream, independent reconciliation and final normal return remain pending.
Do not restart that runner or interpret its absence of acceptance as failure.

While that native backup is running, host-only derivative qualification has
been added in `forge_recovery_derivative.py`. It verifies the complete pinned
original archive (including gzip completion and every root chunk), compares a
private candidate image, rejects changes to any protected prefix/suffix byte,
and seals separate original/derived hashes and changed-root-chunk indexes.
Source files are read-only; source mutation, missing bytes, invalid permissions,
or lease callback failure leave incomplete evidence. This proves root-only byte
lineage, not filesystem health, native boot compatibility or deployment authority.
The new manifest deliberately cannot masquerade as an original backup in the
existing restore compiler; derivative dispatch/integration is still pending.

Export now records original source capacity at import and removes QEMU's
power-of-two padding only after checking every primary partition bound and
verifying that the padding contains no data. A real `qemu-img` round trip tests
non-power-of-two export, and a used-padding case refuses publication while
retaining the source overlay. Legacy pinned bases are checksum-verified before
inferring capacity; metadata without capacity/provenance keeps the earlier
full-capacity behavior. New workspaces/base images/exports also receive private
permissions. These changes do not alter the already-running physical trial.
Focused native macOS verification passed 54 tests, including real `qemu-img`
export and privacy checks (`macos-capacity-derivative-tests-r3-20260925.log`).
The earlier revision passed 1,355 Linux tests; the final Linux rerun passed
all 1,356 tests in 107.359 seconds, retained in
`full-tests-derivative-capacity-r2-20260925.log` with terminal zero exit.

Derivative lineage now has a strict host reader and bounded root-chunk stream.
Its sealed manifest includes the pinned original manifest, and loading requires
matching retained inputs plus completed qualification evidence. The rollback
archive must still match its identity/receipts. Prefix verification precedes
delivery; each candidate chunk is hashed before delivery, with final root/card,
suffix, EOF and source-stability checks before stream completion. Consumer or
lease failure remains terminal. These APIs still have no target write route,
filesystem-health claim, native-boot claim or implicit deployment authority.
All 1,368 Linux tests pass in 107.375 seconds
(`full-tests-derivative-reader-20260925.log`); 23 focused native macOS tests pass
(`macos-derivative-reader-tests-20260925.log`). The physical unchanged-source
trial remains in its original backup session while this independent host work
is tested; it must not be mistaken for enhanced-image write qualification.

Fresh archive inspection exposed a packaging defect: `forge-modem.c` and
`usb-net-internal.h` were missing even though the packaged builder declares
them. Packaging now includes the C/header model resources. Native archive
validation parses the packaged builder's literal patch/model declarations and
requires every input before launching anything. The original archive now fails
this stronger check in `native-archive-missing-model-regression-20260925/`;
its earlier launcher/GUI success was not proof of build-input completeness.
The full Linux suite passes 1,370 tests in 106.436 seconds
(`full-tests-package-model-sources-20260925.log`).

Corrected fresh Linux ARM64 and native macOS ARM64 archives are retained in
`build/package-model-sources-r2-20260925/`. Both pass launcher, MCP/resource,
packaged-skill and Tk edit/save validation, including all 27 declared QEMU
inputs. Linux also passes the actual outside-checkout window/edit/save test.
Archive SHA256 values are Linux
`7b549cb90dd9be2270ec293d2b9a37d534f998829ba9e59f87a7a67dd68fbdad`
and macOS
`85cb5ff936ed75949f595f6178a1117b96bfdf148d4a3eea394454a40e375ab3`.
Mac host utilities were compiled natively; the device firmware was reused with
its verified Linux build provenance, not claimed as a Mac firmware compilation.
Evidence is `native-archive-model-sources-r2-20260925/`,
`installed-workbench-model-sources-r2-20260925.log`, and
`macos-native-archive-model-sources-r2-20260925.json`.
The Linux archive's packaged builder then compiled QEMU outside the checkout
into `packaged-qemu-model-sources-r2-20260925/`. All 27 input hashes match the
checkout; watchdog, PMIC, ADC and ADC-migration tests pass against that build
(`packaged-qemu-model-tests-r2-20260925.log`). This does not yet qualify a full
installed guest workflow or release-archive upgrade across every host.

The same archive-built QEMU also passes the broader device gate in
`packaged-device-gate-20260925.log`: 50 CPU lifecycle processes with ten cycles
each, PMIC migration, GPIO interrupts and firmware GPIO bounds/reset, GIC
debugger reads, USB remote wakeup and frame clocks, audio reconnect/capture
device construction, and both keyboard reset-request paths with 17
firmware-derived reports through DWC2. These are model/protocol checks, not
physical-device fidelity or guest ALSA qualification. Keyboard DFU transition
and live host input mapping remain outside this evidence. The actual WAV
callback and QEMU buffered-header failure/clean-exit checks also pass
(`packaged-wav-gate-20260925.log`).

The corrected Linux ARM64 archive then passes an installed GUI-owned guest
capture workflow using that archive-built QEMU, with checkout resource/import
overrides removed from the launched processes. Packaged MCP initialization,
maintenance boot, guest probe upload, exact synthetic capture before and after
disconnect/reconnect, normal shutdown, root ext4 clean-state/superblock-checksum
checking and unchanged base-image verification pass; no GUI owner is retained.
This is not a full filesystem check. Evidence is
`installed-capture-model-sources-r2-20260925/installed-capture-acceptance.json`.
This qualifies capture on this Linux ARM64 package, not the remaining native
host matrix, duplex candidate-driver integration or modified-image hardware
deployment.

Root-only derivatives now have a separate read-only health checker in
`forge_recovery_derivative_health.py`. It consumes the verified derivative
stream, retains a private root copy, independently checks the copy's exact
length/hash and identity, and runs `e2fsck -f -n` through a read-only descriptor.
Evidence is bound to the derivative manifest and root/card hashes, with no
repair, target-write or normal-boot-release authority. Unchanged boot bytes are
not claimed as a boot-filesystem health check. Source/copy mutation, lease
failure, abnormal checker exit and insufficient space cannot publish completed
health evidence. A normal nonzero checker exit remains explicit failed health.
The focused derivative tests pass 33 cases, including a real Linux ext4 check
(`derivative-health-tests-20260925.log`); native macOS passes 32 with the
Linux-only real-checker case skipped (`macos-derivative-health-tests-20260925.log`).
Physical derivative dispatch and native enhanced-image qualification remain
open; this host checker does not alter the running physical restore trial.
The full suite passes 1,380 tests in 108.874 seconds under Tk-capable system
Python with Xvfb (`full-tests-derivative-health-r2-20260925.log`). The first run
under the shell's Homebrew Python failed 19 imports/cases because `_tkinter`
was unavailable; its log is retained, not counted as a passing host gate.

On 2026-09-26, the derivative health reader gained strict external-pin,
source/hash, boolean and supporting-record checks. It permits cleanup of the
disposable root copy without treating historical health as current source
identity or write authority. The full suite passes 1,383 tests in 107.034 seconds
(`full-tests-derivative-health-reader-20260926.log`); the 36 focused cases also
pass on macOS with one Linux-only checker case skipped. A separate
`forge_recovery_deploy_plan.py` compiler reuses the physical rollback compiler's
hold/identity/layout invariants and binds clean derivative root health plus the
original rollback manifest. It refuses a changed current root, changed suffix,
nonclean derivative or unchanged image, retaining a distinct plan kind that the
backup-only worker rejects. Five focused plan tests pass
(`deploy-plan-tests-20260926.log`); derivative worker dispatch remains open.

Deployment plans now also have exclusive private journals retaining all source,
health, hold and boot observations. Loading requires the independent plan pin
and recompiles every input; changed or linked records cannot admit a plan.
The combined 44 focused tests pass on Linux and macOS (one Linux-only checker
case skipped on macOS), recorded in `deploy-journal-tests-20260926.log` and
`macos-deploy-journal-tests-20260926.log`. This still supplies no target writer.
The full Tk-capable Linux suite passes 1,391 tests in 144.398 seconds
(`full-tests-deploy-journal-20260926.log`).
The verified physical backup was materialized to
`forge-enhanced-source-20260926/image.img` with exact original length and SHA256,
then referenced by a new private copy-on-write
`forge-enhanced-workspace-20260926/`. This is preparation for guest enhancement,
not proof of a modified image or native deployment; the original archive remains
intact and the live restore trial was not restarted.

The new workspace passes the GUI-owned MCP enhancement/export acceptance:
`forge-enhanced-workspace-20260926/gui-attachment-f5f83fb85f56442ab8c02c4da9d4563d/record.json`.
An attached client boots the guest, verifies restricted grants and file access,
binary transfer, competing-client exclusion and cancellation, then installs and
tests `/usr/local/bin/attached-forge-17ccfe8489094a4fa8ebf2e7609a407d`, cleanly
stops the guest and exports the image. The export SHA256 is
`85e87149951a8cc7b8776872851cf10ef7e70b1107c12f643df5c02cc2330e3b`.
Native config/cmdline/kernel/DTB hashes are unchanged; root clean-state and
superblock checksum checks pass, not a full filesystem check. Export boot-back
and full root-only derivative/archive-lineage qualification are running in
`forge-enhanced-bootback-20260926/` and `forge-enhanced-derivative-20260926/`.
This is not yet proof that the modified image runs on physical hardware.

Export boot-back subsequently passed: the application digest and execution
matched, shutdown was clean with no forced cleanup, and the exported raw image
remained unchanged (`forge-enhanced-bootback-20260926/acceptance.json`). Full
derivative/archive comparison also completed: 19 root chunks changed, with all
protected prefix/suffix bytes unchanged and original rollback lineage retained.
Derivative manifest pin is
`541a8db7d699eb2267d45bf6987589c9ac93e84dcfd3d90fd36dadf4fe74ec81`;
root SHA256 is
`0724363ea1d754ee6fdcf6b8ce3f87afdfff3c3c5839888191af32117ca74c1c`.
The full read-only root filesystem check is now running against a separately
verified private copy (`forge-enhanced-health-20260926/`), not the card.

The full derivative root check subsequently passed all five e2fsck passes with
exit 0, no repairs or target writes. Its health acceptance pin is
`37bf46ce8f73b7fc60270a7f9d4c1ed215256760ae49653d85fd03a053bf68e6`.
The strict health reader accepted the retained evidence against the derivative
manifest. After the checker exited and no users of its copy remained, only
`forge-enhanced-health-20260926/root.img` was removed (31,373,918,208 bytes);
the final export, original backup and all records remain, including
`copy-cleanup.json`. Native modified-image deployment remains unqualified.

A fresh build from the corrected macOS archive exposed a further portability
issue: BSD patch rejected the under-contextualized DWC2 detach hunks. Adding
surrounding context, without changing their C edits, passes the real macOS
dry-run against the stopped build tree and forward/reverse/shifted-context
regression tests on Linux and macOS. The failed build log is retained as
`macos-packaged-qemu-r2-20260926.log`, not counted as qualification. A replacement
Mac archive has been built (`macos-package-patch-context-r3-20260926.log`);
archive validation and the new complete native QEMU build remain required.

The next Mac build passed the detach patch and exposed the same context issue
in `forge-modem.patch`; the remaining sparse hunks in `usb-net-composite.patch`
were corrected at the same time. BSD patch now applies both to disposable
copies of the actual build inputs. The resulting `meson.build`, `dev-network.c`
and `hcd-dwc2.c` hashes exactly match the already-qualified Linux build tree
(`macos-patch-context-source-parity-20260926.log`), establishing unchanged C/build
semantics rather than merely patch exit success. The replacement archive/native
validation/build chain is running in `macos-package-qemu-context-r4-20260926.log`.

That native macOS chain completed: all patches applied, 2,063 build targets
finished, and QEMU/qemu-img version checks passed. The actual extracted archive
also passes native launchers/MCP/resource/Tk edit-save validation. Its SHA256 is
`49d282b61ad0bbef04bd4cac2f72c09e4df2f506633a86a93812fc3c5b2620f3`, retained in
`build/package-context-r4-20260926/`. Watchdog, PMIC/thermal and ADC migration
tests pass against this archive-built binary
(`macos-packaged-device-context-r4-20260926.log`). Installed guest capture is
running separately; neither this build nor diskless tests prove that workflow.
The full Linux regression suite passes 1,392 tests in 107.447 seconds
(`full-tests-dwc2-context-20260926.log`).

Release packaging now requires extracted archive/GUI validation plus a QEMU
build and watchdog/PMIC/ADC-migration tests from the extracted resources on all
three published host targets. Qualification logs are retained independently of
publishable archives, and publication still depends on the complete package
matrix. YAML structure, target matrix, non-optional gate steps and embedded
shell syntax were checked locally; hosted execution remains unverified until
the candidate revision runs in CI.

The corrected Mac archive's installed GUI-owned capture workflow subsequently
passed (`macos-installed-capture-context-r4-20260926.json`): packaged launchers,
MCP initialization, guest capture before/after reconnect, clean shutdown and
unchanged base all verified, with no retained owner. Its archive-built QEMU
SHA256 is `3fd8b9a82f909bc70787b6d0f71efb9bd45db04e538b3daca260b143d1beb785`.
This closes that Mac capture/build gate, not duplex-driver integration, the
whole native host matrix or enhanced-image physical deployment.

The matching current Linux ARM64 package was rebuilt and passes native archive
launcher/MCP/resource/Tk validation in `linux-native-archive-context-r4-20260926/`.
Its SHA256 is `bc33b31dd3e550f21e488128887730d8ebd8b2c13735b010aaa7115c4d66eddc`,
retained beside the Mac archive in `build/package-context-r4-20260926/`.
A fresh generation is building from those extracted Linux resources, reusing
only the checksum-verified upstream source tarball, not an old compiled cache
(`linux-packaged-qemu-context-r4-20260926.log`).

That Linux archive build completed, and its watchdog, PMIC/thermal and ADC
migration tests pass (`linux-packaged-device-context-r4-20260926.json`), with
QEMU SHA256 `9ea830a2234dddc97b41c9ade232dac51820d2af18591efa260a807c27f73e80`.
An installed GUI-owned capture run is in progress for that exact archive/build.

`forge_recovery_source_contract.py` now provides an explicit owner-selected
backup-versus-derivative source contract for the forthcoming shared transport.
It retains distinct completion statuses, rejects cross-kind and type-confused
receipts, and returns the original rollback manifest without relabeling derived
bytes. It is not yet imported by the physical writer; the live trial's code is
unchanged. All 48 focused source/deployment tests pass locally
(`source-contract-tests-20260926.log`).

The refreshed Linux archive's installed GUI-owned capture workflow also passes
(`linux-installed-capture-context-r4-20260926/installed-capture-acceptance.json`):
capture/reconnect, normal shutdown, unchanged base and no retained owner were
verified against the exact archive-built QEMU. The source-contract full suite
passes 1,396 tests in 107.383 seconds (`full-tests-source-contract-20260926.log`).

The shared chunk writer and byte protocol now support a derivative only when
the owner explicitly selects that source kind at construction and receipt.
Backup-only defaults still reject derivatives, and completion statuses remain
distinct. A real process-pipe test installs only the changed chunk in a
disposable root file; truncated streams and wrong completion statuses cannot
produce success. All 56 targeted protocol/backup/bootstrap/observation tests
pass (`derivative-protocol-tests-20260926.log`). The physical executor still
accepts only backup plans: source-kind support alone enables no derivative
dispatch, device opening or recovery-hold release. The live hardware worker
continues its already pinned code snapshot.

The next executor revision now recompiles either complete backup or derivative
plan evidence through `forge_recovery_operation_contract.py` before device
access. Both distinct operation kinds share the boot-wide ledger, identity,
exclusive-root, held-boot-file and protected-range checks. The source kind is
selected by the pinned operation, never by incoming chunk frames. Worker
bootstrap, host duplex exchange and observation validation use the same
contract; derivative host dispatch and public GUI/MCP wiring remain absent.
Disposable-file execution and real duplex-pipe tests pass, including clean
modified-root completion, explicit renewals/half-close, altered health refusal
before a claim, and boot-wide rejection after an interrupted derivative write.
There are 31 focused passes (`deploy-executor-duplex-tests-20260926.log`) and 194
backup-restore regression passes (`operation-contract-backup-tests-20260926.log`).
The preceding protocol revision's full suite passed 1,401 tests in 107.910
seconds (`full-tests-derivative-protocol-20260926.log`); the executor revision's
full suite is running separately. These are not native deployment claims.

The executor revision's full suite subsequently passed 1,407 tests in 108.986
seconds (`full-tests-deploy-executor-20260926.log`), and 31 focused native Mac
cases passed (`macos-deploy-executor-tests-20260926.log`).

Installed owner code now has a separate `deploy()` host entry point. It loads
only a derivative journal, binds the exact approved health pin, and re-verifies
the entire derivative and original rollback archive lineage while renewing the
same owner's lease before dispatch. Approval explicitly names `deploy`, source
health and the original rollback manifest; a backup `restore` approval cannot
substitute. All attempts use exclusive durable intent and preserve uncertainty
on failure, with no automatic retry or hold release. Read-only reconciliation
loads/recompiles either operation kind and checks the corresponding retained
health. The initial 35 targeted dispatch/executor/backup/reconciliation tests
pass (`deploy-dispatch-tests-20260926.log`); additional preflight lease and
rollback-mutation cases plus the full suite are running. This internal entry
point has not yet been qualified on physical derivative writes or exposed by
the public GUI/MCP workflow.

The dispatch revision subsequently passed all 1,416 tests in 109.593 seconds
(`full-tests-deploy-dispatch-20260926.log`), and all 37 focused native Mac cases
passed (`macos-deploy-dispatch-tests-20260926.log`). A new disposable recovery
probe now prepares a real, clean ext4 root derivative with retained original
archive lineage, streams it through the shared production worker, independently
hashes it, and restores the original backup. Its optional lost-completion case
requires the exact injected boundary and a matching durable completion fence;
it never retries the deployment. Physical compiler/identity boundaries remain
explicitly substituted. This does not qualify production host dispatch, native
application boot, or physical derivative writes. The 12 probe tests pass,
including a real Linux ext4 check and physical-target refusal
(`deploy-stream-probe-tests-20260926.log`); recovery-VM qualification is running
in `recovery-derived-root-roundtrip-20260926/`.
The full suite passes 1,420 tests in 109.966 seconds
(`full-tests-deploy-probe-20260926.log`); native Mac passes the probe suite with
the Linux-only ext4 case explicitly skipped
(`macos-deploy-probe-tests-20260926.log`).

The first recovery-VM run reached durable derivative completion, lost its reply
as intended, and recovered the exact completion fence without retrying. Its
independent root, whole-card and protected-range digests match the derivative.
However, the new harness incorrectly retained the original whole-card digest
in its expected post-deployment observation, so it stopped before rollback.
That failed run and synthetic card are retained; no physical card was involved.
The harness now expects both the derived root and derived whole-card digest,
while requiring unchanged protected ranges and boot identity. A regression test
covers each field, and all 13 focused probe tests pass
(`deploy-stream-probe-tests-r2-20260926.log`). A fresh synthetic-card roundtrip
is running in `recovery-derived-root-roundtrip-r2-20260926/`; it also requires
modeled watchdog exit after the roundtrip, not forced cleanup.
The corrected-hash revision passes all 1,421 tests in 110.078 seconds
(`full-tests-deploy-probe-r2-20260926.log`) and the focused native Mac rerun
(`macos-deploy-probe-tests-r2-20260926.log`).

The derivative probe now also has a distinct interruption trial. Its clean
ext4 fixture places changed metadata in multiple chunks, and independently
predicts the exact partial root after chunk zero. The worker is killed after
that chunk's fsync/readback but before acknowledgment. The trial requires an
incomplete durable fence, rejection of another writer in the same boot, an
explicit new recovery boot, rejection of the stale worker, unchanged partial
bytes across reboot, then original-backup restoration and full-card equality.
No automatic deployment retry is used, and incomplete host evidence is retained.
All 16 focused cases pass on Linux; Mac passes with two explicit Linux-only
skips (`deploy-interruption-probe-tests-r2-20260926.log`,
`macos-deploy-interruption-tests-20260926.log`). The full suite passes 1,423
tests in 110.334 seconds (`full-tests-deploy-interruption-20260926.log`). The
recovery-VM trial is running in `recovery-derived-root-interruption-20260926/`;
these unit results do not establish its completion or physical deployment.

The corrected lost-completion recovery-VM roundtrip subsequently passed
(`recovery-derived-root-roundtrip-r2-20260926/acceptance.json`, terminal exit 0).
It deployed a clean derivative, recovered the exact durable receipt after the
injected lost reply without retrying deployment, independently matched the
derived whole card, then restored the original card byte-for-byte. Exactly one
4 MiB chunk was written in each direction; protected ranges remained unchanged.
Hold release and modeled watchdog exit also passed, with no forced cleanup.
This is emulator worker/protocol evidence, not physical deployment, native
application boot, or production host-dispatch qualification.

The interrupted derivative VM trial also passed
(`recovery-derived-root-interruption-20260926/acceptance.json`, terminal exit 0).
Its healthy derivative changed seven source chunks. SIGKILL followed the first
4 MiB chunk's durable write/readback, leaving an independently verified partial
root and an uncertain host attempt. Same-boot re-entry and an old-boot worker
were rejected; a fresh recovery boot preserved the exact partial bytes, then
restored the original whole card. Hold release and modeled watchdog exit passed
without forced cleanup. The physical worker and image were not touched.

Release packaging now also supports manual and pull-request qualification runs.
Publication is restricted to a version-tag push, so a final branch revision can
exercise all three native package jobs before tagging. Local YAML/dependency,
publication-guard and shell-syntax checks pass; hosted execution is still
unverified. A read-only GitHub baseline found no open fork PRs and latest stable
tag `v1.0.2` (`release-open-pr-baseline-20260926.json`,
`release-baseline-20260926.json`). This is only a baseline, not the final
exact-revision contribution/readiness audit or release authorization evidence.

Host CI now runs the complete discovered suite on both Linux (under Xvfb) and
native Mac Tk, instead of selecting only four Mac modules. Linux installs the
filesystem/image tools needed for the real fixtures. `make check` accepts an
explicit `PYTHON` interpreter, retaining `python3` as its default. The actual
Linux command passes 1,423 tests plus shellcheck in
`make-check-native-ci-20260926.log`; native Mac passes 1,423 discovered tests
plus shellcheck in `macos-full-native-ci-20260926.log`, with 89 recorded skips.
Those include Linux-target/procfs/peer-credential/ALSA tests and two unavailable
optional Mac filesystem-tool fixtures; they are not counted as Mac passes.
Workflow syntax checks pass, but hosted execution remains a separate gate.

Draft qualification PR #1 now carries commit `83d966d` and starts hosted
pre-publication testing. Host CI run `36229753630` passed its complete Linux and
Mac jobs, but failed Windows portability: two tests executed POSIX guest-session
code, and private image export reached an unavailable `fchmod`. Private image
import/export now refuses a host without the required permission primitive
before workspace/file access; it does not substitute ineffective Windows chmod
bits for a private ACL. The POSIX execution tests are explicitly scoped, while
portable helper checks and refusal coverage remain on Windows. Windows is not
an advertised forge package host. Fresh CI for this correction is still needed;
the original failed run is retained, not counted as a pass.

The subsequent `cf53aec` revision passes hosted Host CI (`36230138664`) and
the complete three-platform package workflow (`36230138661`). Publication was
skipped, as required for a PR. Downloaded archives match their native acceptance
hashes; installed GUI/source editing, packaged source completeness, and
archive-built QEMU watchdog/PMIC/ADC-migration checks pass on all three runners.
Evidence and archives are retained in `ci-cf53aec-20260926/`. Archive SHA-256:

| Target | SHA-256 |
| --- | --- |
| Linux x86-64 | `1090df732c3155f14d0544159db4fc87c41e3e05659257c99311491ea1169b53` |
| Linux ARM64 | `27a181cad3f28b38bb2295491927d61c2e57b6d96d6a0b3b7a003abee5869d80` |
| Mac ARM64 | `f3f07fb68500b9df8743b00e89bc989f9ccf194bbb2c75e285d217110c01dd45` |

These qualify that revision and those checks, not subsequent source changes,
full guest desktop acceptance on x86-64, or the unfinished hardware image loop.

Foreground recovery jobs now have a durable session primitive in
`forge_recovery_session.py`. A private owner-pinned journal binds the exact
connection identity, credentials, RAM boot and lease owner. Its lock spans the
job; each renewal intent is durable before transport, and only a validated
reply becomes an acknowledgment. Reopening preserves an unresolved request;
normal renewal refuses it until an explicit `retry_pending()` resends the exact
request. Historical receipts are not presented as live leases, and journal
failure poisons reuse. There is no background keeper, automatic sequence
adoption, root-write approval, reboot or hold-release capability.

All 40 focused session/lease/dispatch cases pass on Linux and Mac
(`recovery-session-tests-r3-20260926.log`,
`macos-recovery-session-tests-r2-20260926.log`). The full Linux suite plus
shellcheck passes 1,439 tests in 110.836 seconds
(`full-tests-recovery-session-probe-20260926.log`). A real RAM-only QEMU trial
also passes (`recovery-durable-session-20260926/acceptance.json`, exit 0):
competing owner refusal, real target reply loss, journal reopen, implicit-retry
refusal, exact non-extending duplicate, then sequence advancement. No SD image
was attached, and modeled watchdog exit completed without forced cleanup.
This is the session foundation for GUI/controller integration, not a claim that
the public full-image workflow is already wired or physically qualified.

The fresh physical stream trial has now verified its complete 31,914,983,424-byte
backup, compressed to 6,159,787,989 bytes, card SHA256
`10809a79cb0ca8aa6639981966425279d36e4e8f2d8314b723aa85666a1e6df7`.
Independent root SHA256 is
`43aec057497163618ef9e051e2146d41c944f99242af833c0e2bf66749db8da8`.
Source checks report FAT 0 / ext4 4, not a clean filesystem. The verified source
manifest and read-only live-card comparison completed; install-hold is running
under the original lease owner, not yet acknowledged. Disposable materialized
and partition-check copies were removed after checks completed, retaining the
archive and all evidence (`source-health-copy-cleanup.json`). Restore streaming,
reconciliation, normal return and enhanced-image qualification remain open.

The stream trial subsequently acknowledged install-hold with root unchanged
(plan `b8c28b52e79ebd360fa88e74a43ab273cd681b5b6862ec99a3f3c544e17ac0f9`)
and observed normal firmware selection into a new RAM boot
`678e5ed6-1a06-4183-91ea-9a9939704282`. Hold reconciliation is running under the
new bound lease. This is not yet completed restore-stream or enhanced-image
qualification.

The stream trial's hold reconciliation subsequently completed with the original
root unchanged. Its runner then stopped before creating a restore plan or
attempt because it supplied the RAM observation wrapper instead of the raw
observation to the strict compiler. `trial-failure.json` retains this failure.
After confirming the original process had exited, absence of any restore plan,
the same live RAM boot and its last acknowledged lease sequence, the explicit
`continue_after_plan_guard.py` continuation renewed that lease and captured fresh
normal-selection evidence. It preserves all prior journals and corrects only
the failed call's wrapper. The production unchanged-source restore stream is
now running (`physical-restore-stream-continuation-20260926.log`); no completed
stream or modified-image hardware qualification is claimed yet.

The physical hold trial has now completed its install-hold commit with full root
readback unchanged, then rebooted into RAM through **normal** firmware selection.
`physical-persistent-hold-20260925/ram-selection-0.json` records the new boot UUID
`de544c3f-31e4-41af-bfb3-cc4c4e554a9c`, normal selection and partition 1. Its
`install-hold/reconcile-33f0d4af01074be98c2966b6f369b75e/` evidence records
`previous-boot-ended`, held files in the `after` state, image matched, stage absent
and boot unmounted after read-only inspection. Whole-card reconciliation hashes,
hold release, normal OS return and nine-preimage restoration are still running;
this is not yet whole-trial acceptance or root-restore qualification.

That physical hold/release trial has now completed successfully. Its retained
`physical-persistent-hold-20260925/acceptance.json` reports verified backup bytes,
persistent normal-selection RAM boot, unchanged root through hold and release,
normal OS return and all nine boot-file preimages restored. The install pin is
`34b9cd120b8a5873b74b6d8d3771aac2da6b418435a083aafb5ca4fb62c463b4`;
the release pin is
`3e3cf6a957f06de06fbd66b85d10a419e6e1b90754e4840e942cf95b3d057a0b`.
`normal-return.json` verifies normal boot UUID
`b357de70-5821-4517-a861-42528f6e4494`. The harness exited successfully and was not
restarted. Its backup, logs and journals remain retained. Root was never written;
filesystem consistency and root restoration remain explicitly unqualified.

The completed hold-trial backup has now been materialized and checked on private
host copies in `physical-persistent-hold-20260925/source-health-materialized/`
and `source-health/`. The materialized 31,914,983,424 bytes match the verified
card SHA256 `c5ec25df00d7bb7fa5c2d24506343717c550ec7cffc855c065580333cb721eb7`.
FAT check returns 0; read-only ext4 check returns 4 and reports 105 orphan-list
inodes. The inode list matches the earlier full-card backup's read-only check.
This is evidence of pre-existing filesystem errors in the saved source, not a
clean-source qualification or proof of their cause. No repair or target write
occurred. The source-health acceptance digest is
`9df8e89ff99e903c48934d8f9dd25bf75efe7db2ffa909d50f1fe6763e259665`, and the
dispatch health validator accepts its binding to the prepared source manifest
while preserving `filesystem_consistency_qualified: false`. Any eventual
byte-faithful restore still requires explicit acknowledgement of these errors;
a repaired or enhanced derivative must have separate provenance and authority.

The host-only filesystem checker in `forge_recovery_filesystems` now splits a
completed, materialized card image into private boot/root copies. It checks the
copied DOS header against the original recovery layout and rehashes the entire
image (including gaps) before running either checker. Linux `fsck.fat -n` and
`e2fsck -f -n` receive read-only host file descriptors; no mount, repair or target
access occurs. Output and execution time are bounded. A nonzero exit retains
failed check evidence and never grants restore authority. The six focused tests
include actual clean FAT32/ext4 filesystems and invalid-filesystem rejection,
plus changed-source rejection and bounded checker failure. All 167 recovery
tests pass in `build/emulator/recovery-filesystem-tests-20260925.log`. Physical
filesystem consistency is still unverified until the running capture completes
and this check succeeds on that exact archive.
The fresh macOS snapshot `/tmp/uconsole-offline-checks.BPW9MW` passes all nine
archive-materialization tests and the portable layout test; five Linux-only
filesystem-checker tests are explicitly skipped. Log:
`build/emulator/macos-offline-checks-20260925.log`. This confirms portability of
the host archive step, not native macOS filesystem-checker support.
The persistent-hold review compiler now accepts an explicitly pinned lease
owner and checks the staged command line against that exact recipe. It retains
all underlying recovery-recipe gates and the lease's read-only purpose; omitted
or changed owners cannot adopt an already leased staging set. Five hold tests
pass. This remains a review compiler with no dispatch, root-write or normal-boot
release authority; physical persistent-hold qualification is still outstanding.
The refreshed Linux suite, including the filesystem checker and leased-hold
compiler, passes all 1,023 tests in 91.969 seconds. Evidence:
`build/emulator/full-tests-offline-recovery-20260925.log`.
Physical jobs within one controller now reserve the owner-pinned target machine
identity as well as the workspace. File/service transitions and recovery
inspection share the reservation, so different workspaces or SSH aliases cannot
race on the same device; independent machine identities can still run together.
The binding is read from a digest-checked transaction before queueing. Success,
failure, executor rejection and queued cancellation release only the exact
owning job's reservation. Five new tests use real pinned journals with hostname
and IP aliases; all 217 target tests pass in
`build/emulator/target-serialization-tests-20260925.log`. This is same-controller
exclusion, not a cross-host distributed lock. Target-side transaction locking
remains required, and future recovery backup/restore controller jobs must join
the same reservation path.
The complete Linux regression suite passes all 1,028 tests in 89.548 seconds
(`build/emulator/full-tests-target-serialization-20260925.log`). A native macOS
snapshot also passes the five alias/identity exclusion tests and nine target
controller tests (`build/emulator/macos-target-serialization-20260925.log`).

The fresh macOS snapshot passes 943 tests with 50 explicit skips in
`build/emulator/macos-network-recovery-suite-r2-20260925.log`. Its first run and
the earlier GUI-blocked run are retained as failures, not counted as passes.
The source tests canonicalize macOS temporary paths without relaxing production
symlink protections; the test subprocess PATH includes the supported Python.
These results do not close the remaining full-image hardware and release gates.

Treat the physical uConsole as a development target, not a card that the user
must remove. The default workflow uses its existing SSH access, with verified
backup and restore before changes. A spare SD card or reader is not a
prerequisite. External-device flashing remains an optional deployment path.

The normal development loop is edit/build on the host, test in the IDE,
back up the affected target state, deploy over SSH, and test on the device.
Retain the backup and an explicit restore action after a successful deployment
so the user can keep developing on the modified target. Qualification must
exercise that restore action; normal development need not immediately undo
every successful change. Neither removing the card nor reflashing the entire
image belongs in this default loop.

* Record target identity, native boot configuration, storage layout and the
  intended change set. Preserve credentials and unrelated target state.
* Before deployment, back up affected files and metadata, including absence of
  newly created paths, service state and any package state needed for recovery.
  Keep a verified host-side copy and a transaction journal; insufficient space
  blocks writes, not read-only development work. A backup only on the card being
  changed is not adequate disaster recovery.
* Stage the same application/service artifacts tested in the image, verify their
  hashes, and apply bounded changes through shared controller jobs. Record
  target-specific configuration separately; do not enable emulator adapters.
* Validate on the target, including reboot and SSH reconnection when relevant,
  then exercise restore and verify the original state. An interrupted or
  unacknowledged operation remains uncertain until reconciled, never successful
  merely because the SSH connection closed.
* Boot/kernel, partition and whole-system changes require a separately verified
  recovery path that works even if normal SSH fails. Never overwrite a mounted
  system card. A live raw copy is not automatically a consistent backup;
  whole-system capture requires an explicit consistency strategy and restore
  validation before relying on it.

Live artifact deployment and exact exported-image boot are distinct evidence
gates. The former must not be reported as proof of the latter. Full-image
qualification still requires a recoverable deployment/boot path, but may not
silently impose a spare-card requirement on the normal development workflow.
These are implementation and acceptance requirements, not completed capabilities.

Read-only recovery preflight (2026-09-25): `uconsole_hardware_probe.py capture
--ssh jkh@clockworkpi.local --probe recovery --probe storage OUTPUT` now captures
bounded boot configuration, mount layout, binary bootloader properties and
watchdog sysfs attributes. It never opens `/dev/watchdog`, arms a timer, changes
boot selection or claims recovery readiness. Missing paths and read errors are
distinct; binary properties retain their exact bytes and hashes. This is an
inventory, not a consistent backup. Physical evidence is retained privately in
`build/emulator/physical-recovery-inventory-20260925.json`.

The target currently has one 29.7-GiB card with mounted FAT boot and ext4 root
partitions, not a separate recovery partition. The next boot-recovery gate must
prove a one-shot alternate boot plus failure fallback before changing the normal
boot path. Raspberry Pi documents [tryboot](https://www.raspberrypi.com/documentation/computers/config_txt.html)
and [boot watchdog settings](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc),
but availability and successful fallback on this target remain unqualified.
Do not infer them from the existence of a watchdog device or from a successful
normal reboot. No target boot configuration was changed during this inventory.

The non-deploying `forge_tryboot_recipe.compile_recipe` now accepts verified
native configuration preimages and produces an identity-only trial recipe.
It preserves the native kernel/root, copies configuration into a separate
`tryboot.txt`, selects a separate trial command line, and adds a nonce for
observing the selected boot. It rejects existing alternate boot files,
configuration indirection and emulator/init overrides. Native files remain
untouched; the inverse records the original absence of both new files. Three
tests cover preservation, rejection and the explicit unqualified/non-deployed
result. This compiler does not authorize or perform boot changes.

Physical configuration preimages (five paths, including absent trial/autoboot
files) are verified and retained in the private 0600 host artifact
`build/emulator/physical-tryboot-preimages-20260925.json`, SHA-256
`9e546d95cfd1f4d7b04dd18d4f2e8409c41650925572d55ff1189dac0f8009c2`.
Compilation against those real preimages passes; no trial was deployed or booted.
The target's 6.12.62-v8+ configuration has initramfs, gzip initrd, loop and ext4
built in, but Wi-Fi (`BRCMFMAC`) is modular. Its current usable network path is
Wi-Fi. A future RAM recovery environment therefore needs matching modules,
firmware and secured network access, not just a shell. Bootloader version is
2025-02-11; current documentation alone does not prove its watchdog behavior.

FAT transaction prerequisite: the ordinary file publisher previously required
hard links for absent-path no-clobber publication, which FAT cannot provide.
It now falls back only on Linux link-not-supported errors to `renameat2` with
`RENAME_NOREPLACE`; unsupported rename or destination collisions still fail
closed. Other link errors do not trigger fallback. The ordinary boot-path
authoring guard remains in place. The 22 file-transaction tests pass, including
actual no-replace syscall and collision tests, and `validate_target_fat.py`
passes on a disposable loop-mounted FAT filesystem with the physical boot
partition's uid/gid/mask settings. Checks cover creation, exact metadata
readback, retry reconciliation, collision preservation and restore to absence.
The scratch filesystem was unmounted afterward; no physical boot files changed.
This is filesystem qualification, not an applied or booted trial.

Physical staging qualification now passes (no reboot): `prepare_staging`
creates two private, digest-bound transactions from the verified recipe and
preimages. Each guards all five paths; apply installs the command line before
the selector, and restore removes the selector before the command line. The
normal config/cmdline are guard-only entries. Four recipe tests pass, including
dependency ordering and equality of the intermediate state between phases.
The actual SSH executor applied and restored both phases on the uConsole FAT
partition; all four acknowledgements and the frozen plans are retained under
`build/emulator/physical-tryboot-staging-20260925/plans/`. Fresh `after.json`
matches `before.json` for machine identity and every file's contents/metadata
(atime is not a write-conflict field). Both trial files are absent again.
No normal boot file was modified and no reboot was performed. This establishes
the backup/stage/restore mechanics, not tryboot selection, watchdog fallback,
RAM recovery or exact exported-image boot. Boot staging remains internal;
ordinary file-authoring and agent permissions have not been broadened.

Physical identity trial boot now passes: after reapplying the same guarded
plans, a single `sudo reboot "0 tryboot"` returned over SSH with a new boot ID,
the expected unique command-line nonce, the same machine/root/boot partition,
and the device-tree `tryboot` cell equal to 1. The read-only
`forge_boot_observation` verifier rejects old boot IDs, wrong machine/partition,
wrong or duplicate markers, emulator adapters, changed root and wrong flags.
Two tests exercise normal/trial success and those negative cases. Evidence is
`physical-tryboot-staging-20260925/boot-trial.json` and
`boot-trial-verification.json`; reboot intent was recorded before dispatch.
Temporary DNS/SSH failures during startup were not treated as boot failure or
as permission to repeat the reboot. Both trial files were then restored to
absence before requesting a normal reboot. This identity trial retains the
native kernel/root; it is not failed-boot fallback or independent RAM recovery.
The subsequent normal reboot also passes: another new boot ID, `tryboot=0`,
no trial nonce and unchanged native root/partition. `boot-normal.json` and
`boot-normal-verification.json` retain that observation. A fresh host-side
`after-reboots.json` capture matches the original five preimages, including
absence of both temporary trial files. Both authorized reboots and restoration
are complete; no watchdog failure was injected.

RAM recovery runtime resources are now in `forge_recovery_runtime.py`, but are
not yet a built or boot-qualified recovery image. `/init` requires the explicit
recovery marker and provisioned Wi-Fi/public-key/host-key files, mounts only
RAM/pseudo filesystems, starts matching-module Wi-Fi and public-key-only SSH on
port 2222, and schedules a five-minute return reboot. Password authentication,
PAM and forwarding are disabled; normal root is never automatically mounted.
The software deadline cannot recover a hung kernel/firmware and is not a
substitute for the unqualified hardware-watchdog gate. Three tests cover shell
lint, rejecting DHCP events from other interfaces and SSH security settings.
The physical existing initramfs has neither brcmfmac nor SSH; the target's
Wi-Fi module chain includes rfkill/cfg80211/brcmutil/brcmfmac, with RF kill
initially configured blocked. The runtime explicitly unblocks WLAN after
module loading. Building and verifying the complete dependency/credential
bundle, emulator testing, authenticated physical RAM boot and restoration all
remain required before relying on this path. No credentials were read or
embedded and no recovery image was deployed in this step.

Recovery image assembly now works through `build_recovery_initramfs.py`, using
an exclusive private output directory and bounded, private, non-symlinked
credential inputs. The native initramfs-tools hook includes Wi-Fi modules and
CM4 firmware, dynamic libraries, and OpenSSH's separate session/auth binaries.
It does not copy the target's account/password database. The image remains
private and is explicitly marked not boot-qualified or deployed.

The physical target built a 37,239,182-byte test image for 6.12.62-v8+, SHA-256
`78203285a2881578e62dbe021f60a24b52094d21f7babb402b50d33a93d2354d`, using
generated disposable host/client keys and a nonexistent test Wi-Fi network.
Remote build evidence is in `/tmp/forge-recovery-build.8qKtyw/qualification-r3/`;
the host retains its image, manifest and build log under
`build/emulator/recovery-build-20260925/`. The extracted init matches the
RAM-only runtime; module/executable checks and a chrooted `sshd -t` pass.
The offline validator initially failed because `/tmp` is nodev and extraction
under a private umask restricts directory traversal. Its corrected fixture binds
only `/dev/null` for the check and unmounts it afterward; the archive itself has
normal 0755 root directories. The corrected check passed against the existing
image, not a fresh full validator run. Earlier failed builds remain retained.
Five local builder/runtime tests pass. Actual boot, authenticated login,
Wi-Fi association, hardware fallback and deployment integration remain open;
this is build/preflight evidence only. No production credentials were used.

The first diskless QEMU boot found a real missing-mountpoint defect: `/init`
tried to mount `/proc` before creating it. The failure path rebooted, but that
was not accepted as network-boundary coverage. The runtime now creates its
mountpoints first. A fresh full native builder/SSH-preflight run passes in
`/tmp/forge-recovery-build.8qKtyw/qualification-r4/`; its private image and
acceptance are retained on the host in `recovery-build-r4-20260925/`.
Image SHA-256 is
`66d915b38a28323af598c754b40180bbb612000d823b987df526b4a1069862c1`.

`validate_recovery_qemu.py` now pins the image hash, records all executable/
kernel/DTB/image hashes and boots it with no disks attached. It requires the
ordered init, brcmfmac registration, missing-interface failure and kernel reboot
markers, zero QEMU exit, and no mount errors or panic. The actual BusyBox error
differs from iproute2's; the validator was corrected and a fresh run passes in
`build/emulator/recovery-qemu-r4-verified-20260925/`. Prior failed logs remain.
Six local recovery tests pass. This establishes RAM userspace startup through
the absent-WLAN failure and software reboot, not Wi-Fi association, SSH login,
the five-minute deadline, physical watchdog fallback or image restoration.

Authenticated extracted-userspace SSH now passes against that same r4 image.
`validate_recovery_ssh.py` starts the image's sshd and split helpers inside a
chroot and private network/mount namespaces, with only loopback enabled. The
client ignores ambient SSH configuration/agent keys and pins the disposable
host key. Tests demonstrate a rejected unauthorized key, rejected mismatched
host key and a successful authorized root shell (`id -u` returns 0). Server
processes are bounded and reaped; the single `/dev/null` bind is unmounted.
No port 2222 listener or bind mount remains on the normal target afterward.
Retained host evidence: `build/emulator/recovery-ssh-20260925/` includes server
logs and all three client outcomes. Test keys remain in the private remote
fixture, not published artifacts. Eight local recovery tests pass, including
refusal to run the validator in the host namespaces. This qualifies the image's
SSH userspace and authentication, not RAM-booted networking or physical Wi-Fi.

RAM-booted emulator networking and SSH now pass. Only the exact
`uconsole.emulator=1` boot token selects the USB Ethernet adapter; native recovery
continues to select WLAN. The DHCP hook accepts only the interface selected by
the root-owned runtime record. The initial emulator trial used the wrong
interface name (`eth0`); the driver's actual `usb0` name is now used, with the
failed log retained. The builder includes cdc_ether alongside native Wi-Fi.

The r6 private test image (SHA-256
`370c124c5a706c0d73a74bbed8960957601c59db648a98e7c48c5d394040c877`)
passes native assembly/preflight and actual diskless QEMU boot, DHCP lease
acquisition and pinned-key SSH login. The guest's `/proc/mounts` confirms a RAM
root and no mounted block-device filesystem. Evidence is retained in
`build/emulator/recovery-build-r6-20260925/guest-ssh/` and `qemu-network.log`.
Nine recovery tests pass. The VM is being retained to observe the five-minute
software reboot deadline; it has not yet qualified that deadline. No production
Wi-Fi credentials or physical boot configuration changed in this step.

The combined Linux Python regression suite passes all 729 tests in
`build/emulator/python-suite-recovery-20260925.log`. The deadline verifier now
requires a single authenticated recovery boot, a guest-uptime interval of
300–315 seconds from init to reboot, and a zero QEMU exit; panics, explicit
failure-path reboots and host termination/timeout are rejected. Ten focused
recovery tests cover these checks and the earlier runtime/build/SSH gates.

The same r6 VM subsequently rebooted on its own, and QEMU exited zero before
the host's 360-second guard. The controlled software-deadline verifier passes;
measured guest uptime and scope are retained in
`recovery-build-r6-20260925/deadline-acceptance.json`. The localhost forwarding
listener is gone. This qualifies the five-minute software return in a running
emulator kernel, not a physical hardware watchdog or restoration after writes.
The runtime is still read-only with respect to target block filesystems;
unconditional timed reboot must not be reused during an in-place restore.

Private boot staging prerequisite: the physical boot mount currently exposes
root-owned files/directories as 0755 and its fstab uses `defaults`. A disposable
FAT experiment demonstrated that `mount -o remount,fmask=0077,dmask=0077` can
exit successfully while retaining 0022 masks and 0755 access. Remount exit status
is therefore not evidence of privacy. A future staging transaction must verify
a fresh mount, effective permissions and non-root read denial, with persistent
policy in place before copying secrets; no lazy/forced unmount is acceptable.

`forge_boot_privacy.private_fstab` now compiles a narrowly reviewed root-only
FAT mount entry while preserving all other text. It rejects duplicate entries,
escaped/ambiguous syntax, changed sources/filesystems, non-root ownership and
unreviewed mount options. Two tests pass. The real `/etc/fstab` preimage was
captured and verified privately on the host in
`build/emulator/physical-fstab-preimage-20260925.json` (artifact SHA-256
`8fed2760f51ac42ef633ea82381998e0c13e42e37df575627f54b76039f897cc`).
Compilation against it changes exactly one line and is idempotent. Nothing
was applied to the physical target. Mount-policy apply/restore, reboot
persistence and access-denial qualification remain open; this protects against
unprivileged local reads, not raw access by root or physical card removal.

Fresh-mount access control now passes on both the Linux host and physical
uConsole kernel using disposable FAT images in private mount namespaces.
`validate_boot_privacy.py` first proves that UID/GID 65534 can read an innocuous
fixture through the parent directory under 0022 masks, fully unmounts it, then
mounts with 0077 masks and verifies root ownership/0700 modes and an actual
PermissionError. A final fresh 0022 mount restores readability. Non-permission
probe failures cannot count as denial. Mounts are removed and fixtures retained
privately; neither run touched the device's boot mount or fstab. Evidence is
`build/emulator/boot-privacy-20260925/{linux-host,physical-kernel}.json`.
Four compiler/validator tests passed at that stage; persistent-policy and
reboot qualification followed as described below.

The physical persistent-policy backup/apply/reboot/restore/reboot cycle now
passes. `prepare_policy` freezes an fstab-only transaction with original bytes
and metadata retained; the target's `findmnt --verify` accepts the candidate
without warnings. The actual SSH executor applied reviewed plan digest
`c0db780214ef0f419f174e547e84f6082c0c2954601585233eb6c590bc99a842`.
After one normal reboot, the same machine/root/boot partition returned with
0077 masks, root-owned 0700 boot paths and a real unprivileged read denial.
The journaled inverse restored fstab, and a second normal reboot returned with
0022 masks, 0755 paths and successful unprivileged access. A fresh preimage
capture verifies original fstab contents and metadata (atime is excluded from
write-conflict equivalence). Evidence is retained in
`build/emulator/physical-boot-privacy-20260925/`, including the paired journal,
boot identities and effective mount observations. Six privacy tests and six
journal tests pass. No credential image was staged. This qualifies local
permission protection and restoration, not encrypted storage, protected image
publication, RAM-booted physical Wi-Fi or whole-system recovery.

`forge_recovery_image` now supplies an internal bounded image publisher/remover,
without broadening the ordinary file transaction's 8-MiB limit. It requires
private executor-owned source and destination parent, an approved SHA-256/size
(maximum 128 MiB), and an explicit journal staging token. Source and staged
bytes are verified, publication is atomic no-overwrite, directory durability
and final readback are checked, and removal verifies the exact expected image.
Failures retain private staging evidence and are not reported as rollback or
safe retries. Callers must still establish identity, persistent mount privacy,
original absence and durable intent; this primitive is not a deployment CLI.

Five tests cover publication/restoration, wrong digests, existing and late-created
destinations, modified restoration targets, private permissions and bounds.
The disposable FAT validator now exercises image publication, non-root read
denial and return to absence inside its private-mount phase. It passes on both
host and uConsole kernels; evidence is in
`build/emulator/recovery-image-publication-20260925/`. No image was published to
the real boot partition. Owner-journal integration, reconciliation and protected
real-image staging remain required before physical recovery deployment.

The host-side `forge_recovery_journal` now freezes a schema-checked image plan
binding target identity, private fstab revision, boot device, source/destination,
image digest/size, original absence and staging token. Owner approval pins the
canonical plan digest. An exclusive journal lock spans durable intent, transport
and nonce-bound acknowledgement. Restore requires a verified prior publication;
duplicate dispatches, unacknowledged/crashed calls, wrong nonces and damaged
stored acknowledgements fail closed. Uncertain operations cannot automatically
retry or reverse. Six journal tests and all 21 focused recovery tests pass.
These tests use an injected transport, not physical publication. Target-side
identity/policy enforcement, actual SSH integration and explicit uncertain-state
inspection remain required; no general client deployment permission was added.

The owner-supplied SSH worker now enforces the plan's machine identity, exact
fstab hash, compiled persistent private policy, resolved boot device and actual
private permissions/non-root read denial under the shared target lock. It
checks policy again after the effect; post-effect drift produces no success
acknowledgement. Installed owner modules travel over stdin, without copying
image bytes into command arguments. Four transport tests cover pre-effect
refusal, bracketing checks, post-effect uncertainty and request-digest binding;
all 25 focused recovery tests pass.

A real journaled SSH invocation against the restored public boot mount was
rejected specifically for its non-private persistent policy. Before/after
captures prove the generated destination stayed absent and fstab content stayed
unchanged. Evidence is in `build/emulator/recovery-worker-rejection-20260925/`.
The host journal intentionally remains uncertain after a nonzero transport
exit; read-only absence evidence is not an automatic authorization to retry or
reverse. Successful physical image publication and explicit reconciliation are
still separate gates. No private mount policy or credential image was applied
in this negative test.

Successful physical publication/removal now passes with the r6 disposable-key
image. Before changing policy, preflight found that the old `/tmp` source had
been cleared by reboot. The host-retained, hash-verified image was copied into
an exclusive root-only persistent staging directory,
`/var/tmp/forge-recovery-47bca705e51f44609d067636c66349fd/`, and the owner prepared
a new source-bound plan rather than editing or dispatching the stale plan.

After backed-up private-policy application and normal reboot, the actual SSH
worker published all 37,240,445 bytes under approved image-plan digest
`24e6be730e2a3251b4ddddea75f855cd774bdf8d00911cc4636ebbe7bae2302c`.
An independent unprivileged SSH open received PermissionError. The journaled
inverse verified and removed that exact boot copy before policy restoration;
the next normal reboot restored the original masks. Fresh capture verifies
original fstab contents/metadata and destination absence. Evidence is in
`build/emulator/recovery-publication-physical-20260925/`, including the revised
image journal, policy journal, boot identities, denial observation and final
acceptance. The boot copy is removed; recoverable source copies remain on host
and in the private persistent target staging directory. The 25 focused recovery
tests still pass. This did not boot the recovery image or use production Wi-Fi
credentials; physical RAM networking, uncertain-state reconciliation and
whole-system recovery remain open.

Read-only uncertain-operation inspection is now available through the owner
journal/SSH path. It binds observations to the approved plan, request nonce,
machine and current boot ID, reports current policy validity, and inspects only
the exact destination and journal-token scratch name. Files are classified as
absent, matching bytes, different-sized, changed or unsafe; symlinks are not
followed and public-parent file contents are not hashed. Matching bytes do not
prove ownership or authorize removal/retry. Snapshots are separate durable
`inspection-*.json` records; the operation event sequence remains untouched.

The real earlier rejected operation was inspected successfully: both image and
scratch are absent, the restored boot policy is public, and its journal still
ends in `uncertain`. Evidence is the inspection record under
`build/emulator/recovery-worker-rejection-20260925/journal/`. All 28 focused
recovery tests pass, including unchanged uncertainty after inspection and
preservation of partial/matching files. Explicit effect reconciliation/fencing
is not implemented by this observer; absence at one instant alone cannot rule
out a delayed worker. No target image or mount policy was changed.

New recovery mutations now require schema-2 plans and durable target attempt
records. Schema-1 plans remain inspect-only and cannot silently acquire these
new guarantees. Under the shared target lock, the worker provisions a private
root-owned, no-symlink ledger below `/var/lib/uconsole-forge-recovery/<digest>`.
It durably records request intent before invoking the image primitive and saves
the result before acknowledgement. Repeating a completed nonce returns its
receipt without repeating the effect; nonce reuse for another request, missing
receipts, orphaned records and damaged prior receipts fail closed. An incomplete
attempt blocks both the same nonce and a different nonce for that plan.

All 33 focused recovery tests pass locally. The four target-ledger tests also
pass as root on the physical uConsole using disposable fixtures; no boot mount
policy or image publication occurred in that test. Production schema-2
publication, host-side receipt reconciliation and cancellation fencing of a
not-yet-started worker still require qualification. In particular, old schema-1
uncertainty cannot be cleared by pretending that a historical worker used this
new ledger protocol.

Target-side cancellation fencing is now implemented for schema-2 attempts.
A fence saved under the same exclusive ledger lock permanently rejects that
exact not-yet-started nonce/request. It cannot label a running or interrupted
effect cancelled: a held lock refuses concurrent fencing, an intent without a
receipt returns `incomplete`, and a completed attempt returns its receipt
without reversing anything. The SSH worker exposes distinct internal fence
operations that check root privilege and machine/plan identity but do not
require the private mount policy to remain active; they never mutate images.

All 39 focused recovery tests pass. Nine ledger tests also pass as root on the
uConsole, including a separately spawned worker released only after durable
fencing; its effect does not run. Those are disposable ledger fixtures, not a
production SSH cancellation exercise. Host-side journaled fence requests,
receipt/fence resolution and end-to-end interrupted-transport qualification
remain open. No legacy uncertainty was cleared and no boot policy changed.

Host-side explicit reconciliation now durably records a fence request before
transport and validates the target identity, plan, original attempt nonce and
exact receipt. Original dispatch/uncertain events remain intact; a separate
immutable resolution permits restore after proven completion or a new apply
after proven no-start fencing. Incomplete effects remain blocked. Crash-only
dispatch records receive an explicit uncertainty marker before reconciliation;
legacy schema-1 plans remain inspect-only. All 45 focused recovery tests pass,
including lost acknowledgements, malformed resolutions and preserved history.
End-to-end interrupted SSH qualification and owner UI integration remain open;
this change performed no physical target mutation.

Real SSH pre-start fencing now passes on `clockworkpi.local` via
`tools/validate_recovery_fence.py`. A host-journaled simulated pre-start timeout
was reconciled through the production SSH fence worker, then a delayed invocation
of the production target ledger rejected the original nonce without calling its
effect. Repeated fencing returned the identical response; original host history,
target boot ID and fstab digest remained unchanged. Private evidence is retained
in `build/emulator/recovery-fence-ssh-20260925/`; the root-owned target fence is
retained too. This is not an interrupted image-write qualification: the delayed
probe deliberately uses a harmless ledger effect, since the current public boot
policy rejects image writes before they reach the ledger. Mid-effect disconnect
and completed-but-unacknowledged image receipt acceptance remain open.

After reconciliation integration, the full Linux suite passes 770 tests under
Xvfb (`build/emulator/python-suite-recovery-fence-20260925.log`); the focused
recovery suite passes 47 tests, including exact request/receipt binding and
preservation of the applied state after a fenced, never-started restore.

Real image-publication crash boundaries now pass locally and as root on the
physical uConsole, on both ext4 and an isolated private FAT loopback fixture.
`validate_recovery_interruption.py` exits separate worker processes during copy,
after publication but before the receipt, and after the durable receipt but
before acknowledgement. The first two remain incomplete and reject both replay
and a new attempt; partial scratch or published bytes are retained as evidence.
The third recovers the exact receipt without repeating the effect, then restores
the destination to absence. `validate_boot_privacy.py` now includes these FAT
checks. All 48 focused recovery tests pass. Host evidence is retained under
`build/emulator/recovery-interruption-20260925/`, including physical ext4/FAT
results. These are injected process exits, not actual mid-stream SSH disconnects;
no physical boot policy or boot files changed. Production transport/host-journal
composition across these crash boundaries remains to be qualified.

The completed-but-unacknowledged case now passes across an actual SSH disconnect
on the physical uConsole. `validate_recovery_transport_loss.py` combines the
production host journal, target ledger and image primitives using an explicitly
qualification-only transport mapping to private `/var/tmp` storage. After a
2.3 MB publication and durable receipt, the host terminates that SSH connection
before receiving the acknowledgement. A new connection retrieves the exact
receipt; host reconciliation preserves the original uncertainty events and
enables a separately journaled restore. Independent SSH readback confirms the
fixture destination absent, with unchanged fstab digest and boot ID. Evidence:
`build/emulator/recovery-ssh-loss-20260925/`. All 49 focused recovery tests pass.
This does not exercise the production boot-policy worker or interruption during
copy over SSH; those gates remain distinct from this completed-receipt test.

The non-deploying tryboot compiler now also produces a native RAM recovery
recipe bound to a schema-2 image plan and the backed-up machine identity.
Normal config/cmdline preimages remain unchanged; the alternate selector keeps
native kernel/overlays, disables automatic initramfs selection, and names the
exact recovery image with `followkernel`. Its alternate command line selects
`rdinit=/init`, RAM root and the recovery marker, never emulator adapters.
Legacy RAM-file indirection and conflicting initramfs command-line overrides
are rejected. The recipe explicitly withholds deployment authority pending
private persistent boot policy, acknowledged image publication, reviewed
selector staging and independent boot fallback. Six tryboot tests pass. A
compile-only exercise against retained physical preimages also passes; adapting
the old image-plan data into a synthetic schema-2 input for that exercise did
not upgrade its historical journal or authorize any dispatch. No target writes
or boot trials occurred. Firmware syntax follows the
[Raspberry Pi boot documentation](https://github.com/raspberrypi/documentation/blob/master/documentation/asciidoc/computers/config_txt/boot.adoc).

Fresh physical watchdog checks distinguish runtime service from early-boot
handoff: sysfs reports the BCM2835 watchdog active with a 60-second timeout,
systemd reports `RuntimeWatchdogUSec=1min`, but kernel `open_timeout` is zero and
the current command line has no handoff timeout. EEPROM configuration has no
explicit `BOOT_WATCHDOG_TIMEOUT`. VideoCore firmware reports May 14 2025,
revision `17084b403fb60475b8ee2641c26049a7d54bf153`. No watchdog device was opened,
timer armed or EEPROM changed by these checks. A healthy native watchdog trial
compiler/stager now adds `kernel_watchdog_timeout=120` only to the alternate
selector, with normal native root and inverse file-restore order preserved.
Its verifier requires a new, nonce-matching native tryboot, the exact firmware
command-line timeout and kernel parameter, and an active BCM2835 watchdog owned
by PID 1. Eight recipe tests and four handoff verifier tests pass. The physical
healthy-handoff trial is next; even a pass will not prove failed-kernel fallback.

The physical healthy-handoff trial has now run and **failed its handoff gate**.
Fresh five-path backups and two acknowledged guarded apply phases preceded
tryboot. Boot `0f08b314-8ea0-44aa-bd89-be247101a0c9` had tryboot=1 and the exact
trial nonce `f05ddaac7944410896414f89a30fab10`, so alternate selection worked.
Firmware `get_config int` reported `kernel_watchdog_timeout=120`, but the kernel
command line lacked `watchdog.open_timeout`, and its module parameter remained
zero. The runtime watchdog was active at 60 seconds and owned by PID 1; this is
not firmware handoff evidence. Both alternate files were then restored to their
original absence through acknowledged inverse phases, followed by a normal
reboot and exact preimage comparison. No EEPROM changes or deliberate failure
injection occurred. Evidence is retained in
`build/emulator/physical-watchdog-handoff-20260925/`. The resumable owner harness
is `tools/validate_watchdog_trial.py`. Do not rely on this firmware/configuration
combination for early-kernel fallback; investigate the missing handoff before
booting a recovery kernel that could strand the target.

Read-only follow-up identifies a firmware-version gap: upstream commit
[`3885768a3aad70f42a9d9a85b550046b6b9d83fe`](https://github.com/raspberrypi/firmware/commit/3885768a3aad70f42a9d9a85b550046b6b9d83fe),
dated July 7 2025, introduced firmware kernel-watchdog configuration, after
the installed May 14 firmware. The EEPROM release notes introduce boot-watchdog
support on July 3 2025, after this device's February 11 EEPROM; subsequent July
17 changes add an early-watchdog property requirement and fix Pi4 timer handoff.
Thus accepting a key in `get_config int` was not a valid capability test. The
installed start4.elf SHA256 is
`c815fd71646a0ac30a7fbef2cc10dc3cc2ca2869138b6e2c6f08b881542a0e43`, paired fixup4.dat
`b8dad2df70bc76e717a286d2983f73649f219aea52fd73e22784a17b33671eb7`.
The CM4 EEPROM updater refuses ordinary update mode and recommends rpiboot;
no enable flags, SPI overlays or EEPROM writes were applied. A newer matched
firmware/DT configuration must be qualified through a recoverable workflow,
not inferred safe from version dates. Recovery inventory now separately records
runtime state, kernel open timeout and bootstatus while explicitly withholding
handoff and failed-boot qualification. Four inventory tests pass.

A second healthy physical trial isolates the DT opt-in prerequisite. The actual
CM4 DTB maps `dtparam=watchdog=on` to the watchdog node's `early-watchdog`
property; the first recipe omitted it. The compiler now emits that opt-in and
the verifier requires the property. Boot
`a92b8195-eea2-47e7-9f54-9e0a13d9c6bb` had the correct tryboot nonce and
`early_watchdog=true`, but still no firmware-supplied timeout and kernel
`open_timeout=0`. This rules out the missing DT opt-in as the sole explanation
of the installed firmware's handoff failure. Both guarded inverse file phases
completed, and the subsequent normal boot and exact preimage restoration were
verified. Evidence is retained in
`build/emulator/physical-watchdog-optin-20260925/`. No firmware binary, DTB or
EEPROM was replaced. Eight recipe and four verifier tests pass. A matched
alternate start/fixup pair is a possible next qualification route, but is not
yet staged or proven; the old EEPROM also lacks bootloader-phase watchdog
coverage, independent of a future kernel-handoff result.

Alternate firmware staging is now implemented but has not been dispatched.
`forge_trial_firmware.py` fetches start4/fixup4 from one exact upstream commit,
binds each member's origin/size/hash, and prepares four reviewed guarded phases:
start, fixup, command, selector. Restore removes the selector first and the pair
last. All nine paths remain guards in every phase, including host-backed-up
normal firmware and the absent alternate destinations. Existing custom firmware
or GPU-memory selections are rejected rather than silently reinterpreted.
The normal pair is never replaced, and no EEPROM operation is exposed. Three
new tests cover ordering, all guards, immutable input revision and pair-content
rejection. Physical boot remains gated on a power-cycle recovery path; the user
has been asked whether physical access is available. This is not a requirement
for a spare card or an external reader.

The resumable watchdog harness now prepares alternate firmware using an exact
revision, captures all nine preimages, retains the verified firmware bundle,
and uses the extended guard set when validating restore. Alternate apply and
trial reboot require explicit confirmation that physical power-cycle recovery
is available; restore is allowed without that flag and before any boot trial.
Pinned revision `bead686816848038563a542dc854346ab13253a2` was fetched and four
plans prepared locally from fresh physical backups under
`build/emulator/physical-alternate-firmware-api-prepared-20260925/`.
No dispatch events exist. The first raw-URL attempt failed on a redirect/404;
the retained earlier preparation directory was not overwritten. Fetching now
resolves the exact revision via GitHub's contents API, retrieves each immutable
Git blob, verifies the Git object hash and size, then records SHA256 provenance.
Five pair tests and three harness tests pass. This is prepared evidence only,
not firmware boot compatibility or permission to skip the recovery-access gate.

After the user confirmed physical power-cycle access, the prepared alternate
firmware trial completed on 2026-09-25. A fresh host-side capture verified all
nine current preimages against the retained backups, and all four phase-plan
digests matched before dispatch. Start, fixup, command and selector phases were
acknowledged; a single one-shot reboot produced boot
`e65520ad-2c6f-4e6f-9d2d-df1fd738afb2`. The nonce-bound healthy-handoff verifier
passed with `watchdog.open_timeout=120`, the early-watchdog DT property and the
active runtime watchdog owned by PID 1. This resolves the healthy firmware-to-
kernel handoff gate for the pinned alternate pair, not failed-boot fallback or
EEPROM watchdog coverage.

All four inverse phases were then acknowledged, followed by one normal reboot.
Boot `afec3411-0d9a-4f05-aa7c-9307cb140fe3` passed normal-boot verification and
all nine restored paths matched their host-backed-up preimages. The normal
kernel open timeout is again zero; the original firmware pair was never
replaced. No EEPROM write, failed-kernel injection or physical power cycle was
needed. Evidence remains in
`build/emulator/physical-alternate-firmware-api-prepared-20260925/`, including
fresh preflight backup, all dispatch journals, handoff verification and normal
restoration verification. Transient mDNS/SSH unavailability during the reboots
was observed, not treated as a failed boot or authority to repeat a reboot.

The alternate-firmware compiler now also has a compile-only native RAM recovery
variant. It combines the reviewed matched firmware pair and watchdog DT/timeout
settings with the schema-2 recovery-image identity/hash dependency, explicit
native initramfs selection and RAM-root command line. Normal boot preimages are
unchanged; emulator adapters are excluded. Private persistent boot policy,
acknowledged image publication, independent fallback and physical access remain
explicit requirements, and deployment authority remains false. Seven firmware,
eight tryboot and three trial-harness tests pass, including mismatched image
identity and modified firmware rejection. No combined recipe was staged.
RAM recovery must additionally take ownership of the armed hardware watchdog:
the existing five-minute software reboot deadline does not satisfy the firmware's
120-second handoff deadline. `recovery-watchdog-ownership` is an explicit gate
before this combined recipe may be deployed for a long-running backup.

A bounded RAM-recovery watchdog keeper is now implemented and included by the
native initramfs builder. It requires both recovery command-line markers before
opening `/dev/watchdog0`, checks BCM2835 identity and required driver options,
sets/reads back a supported timeout, and emits readiness only after keepalive
succeeds. It feeds every two seconds for at most 300 seconds and never sends
magic-close or disable requests. The runtime starts it only for the combined
recipe's explicit opt-in and requires readiness before networking/SSH. The
software reboot deadline remains an independent bound. Native C compilation and
injected-operation tests verify expiry, failed keepalive/readiness/sleep and
backward-clock handling without opening a real watchdog. Fifty recovery tests
and seven firmware tests pass before the additional startup-order assertion;
logs are in `build/emulator/recovery-watchdog-tests-20260925.log`.
The updated image still needs native build, diskless boot and physical ownership/
expiry qualification. This fixed-duration keeper does not yet provide the
reviewed renewable lease needed for a long-running whole-system backup/restore.

The updated image now passes native CM4 build, extraction/dependency checks and
SSH preflight with disposable credentials in a fresh `/tmp` build directory;
no boot files were changed. The host-retained image is
`build/emulator/recovery-watchdog-build-20260925/build/recovery.img`, SHA256
`a74e9b11c0ea17071c7f9843b2098b8f183ddb9212d5d24dde19548063e77b53`,
37,246,176 bytes. The keeper executable is present in the extracted image.

Diskless emulator acceptance also passes in
`build/emulator/recovery-watchdog-diskless-20260925/`. Pinned-key SSH confirmed
RAM-only mounts, explicit recovery/emulator/watchdog markers, a live keeper and
active watchdog with 15-second timeout. The harness checked the exact keeper
executable and killed only that disposable guest process; QEMU exited on modeled
watchdog reset without forced cleanup, kernel panic or software reboot. No host
or physical watchdog device was opened by the test. Three C-core/expiry-oracle
tests pass. This qualifies userspace ownership and keeper-loss reset in the
emulator, not physical firmware handoff into this RAM image, physical Wi-Fi or
physical watchdog expiry; those gates remain open.

Private Wi-Fi credential preparation is implemented in `forge_recovery_wifi.py`
but has not captured production secrets. Read-only target inspection identified
an active WPA-PSK profile. The preparer binds machine identity and active profile,
samples security properties and the secret twice, rejects changes, and writes
only to a newly created private directory under a root-owned private parent.
SSID bytes are hex-encoded and passphrases become WPA-PSK derived keys, which
remain sensitive credentials. Errors do not echo key material. Unsupported
security modes/cipher restrictions fail closed. An unresolved PMF global default
is conservatively treated as required, so network compatibility remains unproven;
NetworkManager's [security-setting reference](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless-security.html)
documents the inherited default and PMF enum. Four tests cover a known key
derivation vector, encoding, management-frame policy, private output and profile
change rejection. No credentials were placed on the boot partition, and no
physical network or boot configuration was changed.

Recovery prerequisites are now integrated into the shared controller, attached
client permission boundary, MCP and physical-target panel. The
`target_recovery_inspect` job accepts only a workspace-bound approved transaction,
rechecks its file-plan or service-authorization digest, and verifies the live
machine identity before running the fixed read-only inventory worker. Existing
`target-write` authority is required for SSH execution; the operation itself
does not mutate the target. Reports omit raw boot-file contents and explicitly
withhold backup, firmware-handoff and failed-boot qualification. The GUI blocks
overlapping jobs and closing during inspection, then displays the same report.
Fresh physical transport evidence in
`build/emulator/physical-recovery-inspection-20260925.json` confirms the expected
machine, active 60-second runtime watchdog, kernel open timeout zero, and absent
alternate selector. No target writes or reboot were performed. This is live
worker evidence plus mocked controller/UI integration, not yet a complete live
GUI/MCP recovery-inspection acceptance run or boot-recovery integration.

The live GUI/MCP inspection gap is now closed for the file-plan path.
`validate_target_recovery_inspection.py` invokes the actual Workbench inspection
button, verifies its rendered report against the completed controller job, then
starts a separate stdio MCP owner process and requests the same operation against
the same pinned transaction. Both identify `jkh@clockworkpi.local` and the same
machine, report no inventory errors and withhold recovery qualification. The MCP
process exits 0. Evidence and transcript are retained in
`build/emulator/physical-recovery-gui-mcp-20260925/`; only read-only inspection
operations were requested, not apply, restore or reboot. The validator preserves
failed evidence and rejects an unpinned plan before creating output or a GUI.
The existing Apply/Restore button mapping is preserved and regression-checked;
the new inspection button must not change those hardware acceptance controls.
Boot-recovery deployment, service-authorization live inspection and attached
cross-client inspection remain distinct acceptance gates.

Service-authorization live inspection now also passes. The same acceptance
driver accepts mutually exclusive file-plan and service-authorization pins;
the service path validates the authorization and paired transaction before
creating the GUI or output directory. Workbench and a separate MCP process
both inspected the restored service fixture on `clockworkpi.local`, with no
apply/restore/reboot request and MCP exit 0. Evidence is retained in
`build/emulator/physical-recovery-service-gui-mcp-20260925/`. A changed-host
authorization is rejected before transaction loading or SSH, and all 212 target
tests pass (`target-recovery-service-tests-20260925.log`). These results do not
qualify boot recovery or whole-system backup/restore.

Attached cross-client inspection now passes on the same service-backed physical
target. With `--attached`, the acceptance driver starts Workbench's private
owner socket and a separate `uconsole-mcp --connect` process, restricted to
`target-write`. The client reads the exact completed GUI job, is denied boot
authority, submits its own recovery inspection, and exits 0. Workbench retains
the exact same client job/result in its controller. Both reports identify the
same machine; the independent-owner MCP leg also passes. Evidence is retained
in `build/emulator/physical-recovery-attached-20260925/`, including the attached
transcript and shared GUI job history. All 212 target tests pass. This proves
sequential shared-owner inspection, not concurrent write-conflict handling or
the separate external-agent edit/test/export release gate.

Agent-boundary review found that the serial resource followed `serial.log`
symlinks. The reader now opens its directory and file without following their
final symlink components, uses nonblocking open to avoid FIFO hangs, and checks
the opened descriptor is a singly linked regular file before reading at most
64 KiB. Regression cases reject symlinks and hard links to an outside-workspace
canary, FIFOs and directories, while preserving missing-log and bounded-tail
behavior. This hardens log-resource reads; it is not an OS sandbox against a
process able to modify host paths or the independent coding-agent acceptance
gate.

## External coding agents: shared controls, not an embedded assistant

Read-only MCP discovery now also exposes `jobs`, `tasks` and `targets` resources
per registered workspace, delegating to the existing bounded job-history and
approved-policy calls. Attached-client workspace restrictions and reduced grants
remain authoritative. Live acceptance in
`build/emulator/physical-recovery-resources-20260925/` verifies the GUI job is
visible through the attached client's history resource, approved target metadata
is available, and ungranted host-task execution remains false. The physical
inspection still requests no writes or reboot. Forty MCP tests pass on retry
in `mcp-resource-metadata-tests-r2-20260925.log`; the first run's existing
image-cancellation test exceeded its 15-second future deadline, retained in
`mcp-resource-metadata-tests-20260925.log`. No cancellation timeout was relaxed.

Keep coding CLIs external. Workbench owns the workspace, image lifecycle,
emulator controls, and evidence; the user's chosen coding agent owns its model,
conversation, and code-editing workflow. The GUI must not be required to run
automation.

The existing `tools/uconsole_agent.py` provides machine-readable inspection,
context, tasks, guest commands, and file transfer. The initial stdio MCP server
now uses the same private runtime and image commands, with explicit startup
grants and asynchronous jobs. Its real-client boot/execute/transfer/capture/stop
test passes. Running image, boot and maintenance command jobs now support
cancellation requests. Opt-in local attachment shares the GUI's controller,
with owner-selected grants and per-connection cancellation authority; disconnect
does not stop the owner's VM. Long sessions retire safely persisted terminal
jobs from the bounded in-memory cache without discarding their history.
Some operations remain non-cancellable; the independent coding-agent and
physical image round-trip qualification gates are still open.
Host tasks require a separate grant and owner-reviewed digest-pinned invocation
policy; repository task files are not executable authority. Private durable history supports scoped read-only reconnects,
without treating incomplete records as live work; see [agent setup](forge-agents.md).

Target MCP surface (not all gates are complete):

* Read-only resources: workspace identity, image provenance, runtime status,
  declared fidelity, logs, task definitions, and artifact/validation manifests.
* Tools: prepare/import, boot/stop, execute a guest command, transfer files,
  run a task, capture the display, checkpoint/restore, and export an image.
* Bounded jobs: long operations return job IDs with status, cancellation,
  timeouts, bounded output, and references to retained evidence.

Start with a local, client-launched stdio server. Every operation must identify
its workspace and use owned process/control handles, not arbitrary shared TCP
ports. Serialize guest command traffic and lock image mutations. Inspection is
read-only; guest execution, host tasks, file writes, restore, and export are
explicitly mutating capabilities. Surface their effects to the client and
enforce configured permissions in the controller, not just in agent guidance.
Repository task definitions and guest logs are untrusted input, not authority
to run host commands. Do not include physical flashing in the initial MCP scope.

Ship a companion `SKILL.md` with the adapter documenting actual tool names,
workspace selection, boot modes, the edit/test/export loop, recovery, and the
limits of surrogate evidence. It should explain when maintenance mode is
required and when a normal systemd boot is needed. Include consent boundaries
and treat guest output as data. The skill is workflow guidance, not an access
control mechanism; keep it versioned and tested against the supported API.
Document client registration separately, without making the forge depend on
one coding CLI or requiring an embedded terminal/assistant implementation.

## Release milestones

Readiness assessment, updated 2026-09-26: the modified official CM4 image now has local
evidence for onboarding, 1280x720 desktop interaction, reboot, and normal
shutdown, plus the recorded image round trip. The public-controller physical
backup/restore and original-permission round trip now passes. Candidate `39af129`
passed joined host/GUI and native-package CI (run `36274501227`), with all three
archive checksums, bundled tools, native GUI receipts and repeat builds verified.
This is not yet a published release: final revision review, merge, tag validation
and published-artifact checks remain. Milestone completion
requires evidence for the final implementation; earlier exploratory overlays
underwent several setup revisions and do not qualify later changes by themselves.

Latest qualification update (2026-09-26, owner implementation `b0458fb`):

* Original-root rollback completed with an acknowledged stream, independent
  root hash, native return, enhanced-application absence and all nine boot-file
  preimages restored. Original filesystem errors were preserved, not repaired.
* Public image cleanup succeeded. Permission restoration initially refused
  eight older private trial images. Each was archived, hash-verified and removed
  through its own publication journal. A fresh boot fenced the uncertain old
  request; a separate guarded transaction restored the original fstab and a
  subsequent boot verified original permissions. The failed journal remains
  intact; this legacy recovery is not a pass for the refused public control.
* Fresh public-controller discovery/build, verified image transfer, private
  mount setup/reboot, publication, staging and recovery boot/enrollment passed.
  The fresh current-card round trip completed through the public controller:
  whole-card backup, all 7,481 restore chunks, final root hash, independent
  reconciliation, hold release, normal return, staged-file/image cleanup,
  original fstab restoration and fresh-boot permission verification passed.
  Evidence: `build/emulator/physical-public-roundtrip-20260926/acceptance.json`,
  receipt SHA-256 `dd815ba4b772548633bff35817dc35b9127db8a5c16e7212b403ac642320ad3c`.
  All archives remain retained; no automatic retry or filesystem repair occurred.
  The root already matched this fresh backup, so changed bytes written were zero.
  This qualifies the public backup/restore controls, not another enhanced-image
  deployment; the earlier enhanced native-boot/reimport and changed-byte rollback
  evidence remains separate. The failed legacy cleanup journal remains unchanged.
* The actual Linux ARM64 `/usr/local` installation was upgraded from the verified
  package after archiving the previous installation. All 332 payload files
  match; modes and root ownership match for all 349 entries. An ordinary-user
  launch outside the checkout loaded and saved editable keyboard source while
  leaving bundled source unchanged; the 33,920-byte firmware matches provenance.

| Milestone | Deliverable | Exit gate | Current status |
| --- | --- | --- | --- |
| M0: installation repair | Workbench installs firmware, native flashing tools and editable firmware source | Install the actual archive into a clean prefix, launch outside the checkout as an ordinary user, edit/save source, and verify bundled firmware provenance and freshness | Extracted-archive GUI/source editing and firmware provenance pass in Linux x86_64, Linux ARM64 and macOS ARM64 package CI. The actual Linux ARM64 system-prefix upgrade and ordinary-user edit/save also pass; final release-revision publication remains |
| M1: developer preview | Repeatable official-image desktop through Xorg/fbdev | Close the hardening items below; fresh-overlay onboarding, reboot, keyboard/mouse interaction, application launch and clean shutdown pass; package tests pass on each advertised host | Linux ARM64 and native macOS Cocoa desktop/input/reboot/shutdown loops have evidence. Fresh x86 run 36245917832 passed desktop/input/reboot/shutdown after the UART/QMP backpressure fix, with the stock kernel shutdown warning retained. Final release-revision host matrix remains |
| M2a: dual-target image forge | Persistent image modification, checkpoints and hardware-compatible export | One modified exported image passes the IDE/hardware/reimport loop above; native boot defaults survive; updated kernel artifacts are refreshed; interrupted operations recover safely | Enhanced export independently reconciled on physical hardware, native application hash/output passed, and full reimport/application retest passed. The original interrupted deployment remains uncertain and was never replayed. Changed-byte original-root rollback and native return passed. Fresh public setup/boot/enrollment, current-card backup/restore, cleanup and original boot-permission verification passed. Final release-revision publication gates remain |
| M2b: agent-enabled stable forge | Shared headless controller, CLI, MCP adapter and companion skill | GUI/CLI/MCP parity, workspace isolation, concurrent-client safety, permission boundaries, job cancellation and an external-agent edit/test/export exercise pass; supported host matrix is explicit | A live coding agent used packaged macOS MCP to create and test an application, cleanly export, fully reimport under a separate owner and retest; protected boot bytes and bases remained unchanged. Shared-client controls, cancellation/history and owner-pinned physical staging have evidence. Public-controller recovery round trip and candidate host/package matrix passed; final release-revision publication gates remain |
| M3: power and battery | Versioned scenarios, observable PMIC/regulator/ADC state and events | Production drivers report AC, charge, low battery and power-key transitions; IRQ, reset and failure tests pass | AC/battery states, low-capacity model IRQs, KEY_POWER, sampled temperature and independent thermal shutdown, timed ADC101C conversions/power loss, initial profiles and GUI/MCP host-clock replay tested. Guest low-battery policy, ADC invalid-reference/physical profiles and migration, correlated rails, power-key hold timing and unified/virtual-time scenarios remain |
| M4: keyboard deck | Firmware-derived composite HID/CDC behavior and bootloader lifecycle | Descriptors and reports match captured hardware; matrix, Fn, gamepad, trackball, lighting and simulated update failures pass | Bundled firmware oracle, composite transport and persistent controller/MCP/Tk deck input pass stock Linux A/Fn event checks; descriptors match physical CM4 keyboard bytes. Host-event mapping, broader desktop interaction, live LED changes, DFU and physical report/timing comparison remain; see [contract](keyboard-usb-contract.md) |
| M5: connectivity and audio | Carrier USB topology, audio substitute and SIM7600 application contract | ALSA playback/capture, hotplug, ModemManager discovery, network activation, GNSS/AT scenarios and synthetic fastboot tests pass | USB surrogate playback, synthetic capture and concurrent duplex with exact data/hotplug checks pass through packaged Linux ARM64/macOS ARM64 GUI-owned runtimes and MCP. Lossless playback uses an explicitly loaded candidate guest driver; automatic integration, native routing/jack and other audio/modem gates remain |
| M6: hardware qualification | Revision-specific comparisons and selected real device protocols | Hardware traces agree with declared driver contracts; remaining display, radio, HDMI, storage and expansion differences are measured and published | Physical CM4 Rev 1.1 inventory captured; keyboard device/configuration bytes match the emulator and HID descriptor bytes match compiled firmware. Behavioral traces and the other device comparisons remain open |

M0 can ship independently as an installation fix after its gate passes. M1 is
the first release containing the new display feature; describe it as a CM4
development preview with a surrogate display. M2a and M2b together are the
stable agent-enabled forge gate; each can first ship as a preview increment.
M3–M6 add independently releasable device coverage; they need not delay a
stable application-development tool. Full uConsole hardware equivalence is a
separate claim requiring the applicable physical-comparison gates.

M1 hardening and qualification work:

* Bind setup controls to the QEMU process actually launched. Port collisions
  must fail before touching guest state or logs; exception cleanup must never
  quit another VM through an occupied QMP port.
* Make guest configuration emulator-conditional and explicitly scoped. The original setup
  replaced `display-manager.service` and enabled the surrogate under
  `multi-user.target`; this affects normal boots and exported images as well
  as desktop mode. New setup preserves native defaults and uses a boot marker;
  legacy experimental images are rejected for recovery rather than guessed.
  Finish migration/restoration qualification before release.
* Define desktop account selection after onboarding, X authentication, session
  cleanup, restart behavior, and failure reporting. Verify these behaviors with
  the final launcher rather than inferring them from the first-boot screen.
* Qualify setup interruption, retry and migration from older configuration
  schemas. Preserve useful logs and reject unsupported guest images clearly.
* Test the installed Workbench's automatic setup and display selection, including
  a fresh user data directory and ordinary-user source editing. Include the
  validation record and device plan in packaged documentation. Those documents
  are now included and checked by staged-install tests; qualify the full host matrix.
* Build and exercise release artifacts on the advertised hosts. At `3aeea5a`,
  publication depends on the full host/GUI workflow as well as native package
  qualification; the local release script also requires GUI and emulator device
  gates before tagging. Actual local tests and device checks pass. Record fresh
  joined CI for the final release revision, checksums and known limitations
  before publication.

Dependencies: M0 precedes M1 package qualification; M1 precedes M2a. Controller
and MCP work in M2b can proceed alongside M2a, but stable forge qualification
requires both. The scenario
framework in M3 supports the later device milestones. M4 and M5 can proceed
independently once that framework is established. Collect physical reference
captures early, then qualify each model incrementally through M6.

## Implementation stages

### Stage 1: surrogate display

Use the already emulated Raspberry Pi mailbox framebuffer and the official
image's built-in `bcm2708_fb` driver. Request a 1280x720, 32-bit logical mode on
the kernel command line. The physical panel remains 720x1280 with rotation 90;
the surrogate exposes the final landscape canvas and intentionally bypasses DSI,
panel reset, orientation and backlight.

Acceptance:

* maintenance and normal boots log `Registered framebuffer ... 1280x720`;
* a QMP `screendump` is 1280x720 and contains guest-rendered non-black pixels;
* desktop mode requests `graphical.target`, starts Xorg/fbdev without requiring
  a DRM-backed logind seat, and reaches the image's own RPD X session;
* GTK/SDL and loopback-only VNC show the same scanout;
* command construction and Workbench mode selection have automated tests.

### Stage 2: scenario and observability framework

Initial power profiles now use strict schema-1 JSON, applied to an owned paused
VM before its first CPU execution. Every property is checked for support and
read back; per-run evidence records the source digest, requested values and
observed values. The battery-discharge example passes through real Linux
drivers. MCP boot accepts a files-root-bounded, submission-time snapshot and
retains its digest and state in job history. This is only the initial power
subset: GUI selection is available as a session-local immutable snapshot; timed replay
and the other device domains below remain unfinished.

The shared power interface also supports single-field live changes and sampled
queries through MCP, with a separate `device-control` grant and durable job
evidence. Model readback is checked after changes; failure does not imply
rollback. GUI live controls use the same functions and retain per-operation
evidence. MCP now has a bounded host-monotonic replay scheduler with ordered
single-field events, recorded actual timing and partial-effect cancellation.
Its event schedule is currently a separate document from the initial profile.
GUI replay now uses that same worker with nonblocking Tk polling, explicit
cancellation and conflicting-control guards. Unified machine-scenario documents, guest-virtual-time replay and
the other device domains remain unfinished; host elapsed timing is not hardware
timing fidelity.

Add one versioned machine-scenario JSON document, consumed before QEMU starts,
for battery, AC, thermal, radio, modem, jack, lid/keys and injected faults. Add
QMP commands/events for runtime changes. Every mutable device needs a `query-*`
command so tests can distinguish intended state from a silent no-op.

Extend the hardware probe with framebuffer/DRM, backlight, sound, rfkill,
Bluetooth, serial, block, thermal and complete USB topology. Store sanitized
physical captures by hardware revision and compare semantic fields rather than
unstable bus numbers or timestamps.

### Stage 3: carrier buses, PMIC and battery

1. Apply only overlay fragments whose target controllers exist in QEMU.
2. Extend QEMU's AXP221 model with the AXP228 register subset used by the Linux
   MFD, regulator, AC and battery drivers.
3. Implement ALDO/DLDO enable and voltage registers, IRQ status/mask/clear,
   power-key events, AC present, battery present, voltage/current/capacity and
   charge/discharge state.
4. Make regulator outputs gate dependent models. A disabled display or audio
   rail must have observable consequences rather than always succeeding.
5. Add ADC101C at `0x54`, including conversion value, limits and alert behavior.

Physical baseline (2026-09-25): `clockworkpi.local`, running
`6.12.62-v8+`, declares `ti,adc101c` at I2C address `0x54` and binds `adc081c`,
but reading `in_voltage_raw` returns EIO and `in_voltage_scale` returns EINVAL.
The live device-tree node has no `vref-supply`; the kernel reports a dummy
reference regulator. This explains a missing reference-voltage contract, not
the separate conversion-read failure. Do not interpret driver registration as
proof of a populated, powered, responding ADC. The cause of EIO remains open;
no bus scan, configuration write or physical-target modification was performed.
`build/emulator/physical-iio-r2-20260925.json` retains those failures alongside
valid AXP measurements and both driver identities.

The hardware probe now captures allowlisted IIO raw/scale/offset attributes,
compatible/address/driver identity, and per-attribute errors. Its semantic
comparison ignores IIO indices and I2C bus numbering, but preserves address,
device multiplicity and readings. Identical failed captures remain incomplete,
not matching. Read-only attribute access can request an ADC conversion; it is
not an electrically passive trace. Use `capture OUTPUT --ssh HOST --probe iio`
for a bounded capture without unrelated probes.

Model implementation must retain absent/unresponsive and invalid-reference
scenarios as well as a working ADC scenario. The
[Linux driver](https://github.com/torvalds/linux/blob/v6.12/drivers/iio/adc/ti-adc081c.c)
uses a big-endian conversion word, extracts ten bits from bits 11:2, and obtains
its scale from the reference regulator. The
[TI datasheet](https://www.ti.com/lit/ds/symlink/adc101c021.pdf)
also requires conversion timing, limits, hysteresis and alert state; a direct
QOM-to-register sample would not complete that contract. ADC model and physical
behavior qualification remain outstanding.

The low-level emulator CLI now accepts `run --adc-reference fixed|missing`
(default `fixed`). `missing` selects the boot-only ADC
`reference-supply-present=false` property: it omits the runtime DT reference
regulator/link, without changing the native image or pretending the converter's
analog reference has become zero. The digital converter still uses 3.3 V;
this profile isolates a missing guest reference-supply description. Independent
`adc_powered=false` provides the address-NACK fault, so reference lookup and bus
failure can be tested separately and together. Neither is asserted to explain
the physical target's EIO root cause. Workbench now has an explicit next-boot
selector and MCP `boot` accepts `adc_reference`; both use the shared controller,
validate the choices and freeze them in the boot job context. Changing the
selector after submission does not alter that pending or running VM.

ADC VMState version 2 records the reference profile and rejects a mismatched
destination. Version 1 is interpreted as the original fixed-reference profile.
The first reference-profile guest trial crashed before boot: DT construction
precedes I2C device realization. The corrected machine patch creates/configures
the ADC object before base boot setup, then realizes it on the bus after that
bus exists. The failed trial remains in the Mac qualification host's
`build/emulator/adc-missing-reference-20260925/`; current-profile acceptance is
tracked separately from the earlier fixed-profile evidence below.
The corrected Mac missing-reference trial passes in
`build/emulator/macos-adc-missing-reference-ordered-20260925/`: the guest DT lacks
`vref-supply`, raw values end in 0/512/1023, every scale read reports errno 22,
and independent power-off produces three errno-5 raw reads before recovery.
The base image is unchanged and the stopped root superblock is clean. The eight
ADC migration cases now pass, including missing-profile conversion restoration
and rejection of a different destination profile. ADC register tests also prove
the reference profile cannot be changed after realization; PMIC partial-transfer
regression passes. See `macos-adc-reference-migration.log`,
`macos-adc-reference-qtest.log` and `macos-pmic-reference-regression.log`.
The matching fixed-reference guest regression also passes, including 3.222656250
mV scale, power-loss/recovery, clean stopped root and unchanged base, in
`build/emulator/macos-adc-fixed-reference-ordered-20260925/`.
The real Mac GUI and stdio MCP missing-reference runs also pass, including raw
readings, errno-22 scale errors, independent power-off EIO, replay recovery,
clean shutdown and unchanged base. Evidence is copied to
`build/emulator/macos-adc-reference-gui-20260925/` and
`build/emulator/macos-adc-reference-mcp-20260925/`. The GUI evidence includes the
selected reference profile in durable boot context; MCP retains terminal jobs
after read-only reconnection. Mac backward-stream acceptance now passes too:
`test_emulator_adc_migration.py --v1-source ...` verifies the source lacks the
new profile property, then restores a real version-1 fixed-reference stream
mid-conversion. See `macos-adc-reference-legacy-migration.log`.

Linux now passes the same eight ADC migration cases plus version-1 conversion
restoration (`linux-adc-reference-migration.log`), and both hosts pass the full
emulator gate (`linux-check-emulator-adc-reference.log`,
`macos-check-emulator-adc-reference.log`). The Linux fixed-reference stock-guest
run passes in `build/emulator/linux-adc-fixed-reference-20260925/`. Its missing-
reference GUI run passes in `linux-adc-reference-gui-20260925/`; after that GUI
shuts down cleanly, a new MCP owner boots the same disposable workspace and
passes raw/scale, power-loss, replay and read-only history-reconnection checks.
The base is unchanged; this is sequential owner handoff, not VM adoption.

Fresh native archives pass launchers, GUI edit/save and MCP control/boot-schema
checks; the validator now requires both migration and reference-profile patches.
Evidence: `build/emulator/native-archive-adc-reference-20260925/` (Linux archive
SHA-256 `ef58ac44c19850ff6af6f199b21d302c4ca698bd9e89692cead725022db6938e`)
and `macos-native-archive-adc-reference-20260925/` (Mac archive SHA-256
`cc027ff014161f900db7c4acbdb51b2bde516f276ad2eb8caf2016e2f14fb2e7`).
The Mac archive also builds QEMU from its shipped resources in a fresh isolated
user cache, passes ADC/PMIC migration tests and then the packaged GUI's missing-
reference guest workflow. Its `installed-reference-gui/last-command.json`
identifies that cache. The Linux ARM64 archive now also passes a fresh isolated
user-cache build, ADC/PMIC migration tests and the packaged missing-reference
GUI guest workflow. Evidence is retained alongside its archive acceptance in
`installed-qemu-build.log`, `installed-adc-migration.log`,
`installed-pmic-migration.log` and `installed-reference-gui/`; the launch command
identifies the installed user-cache binary. This does not qualify Linux x86_64,
whole-guest migration, or physical ADC equivalence.

The combined suites exposed a Tk test-fixture lifetime defect: destroyed audio
panel/keyboard-deck widgets remained in Python cycles and were later collected
on an MCP executor thread (`linux-python-suite-adc-reference-trace.log` captures
the abort while garbage-collecting). Those fixtures now clear references and
collect cycles on Tk's creating thread, following the existing Workbench test
pattern. The Mac suite then passes 707 tests with 44 expected skips in
`macos-python-suite-adc-reference-cleanup.log`; failed runs remain preserved.
Linux also passes all 707 tests in `linux-python-suite-adc-reference-cleanup.log`.
This Python-suite result does not qualify Linux's still-older QEMU binary for
the new reference profile.

An initial portable digital core now lives in
`Code/patch/qemu/adc101c-core.h`. The native C harness in
`tests/test_adc101c_core.py` compiles with warnings-as-errors and undefined-
behavior instrumentation. It tests register pointer/word ordering, reserved
bits, incomplete writes, read-only conversion data, ideal half-LSB transitions,
sample/hold versus completion, automatic periods and extrema, strict hysteresis
boundaries, hold/selective W1C/reassertion, disabled/active-low/active-high alert
outputs, invalid voltages, backwards-time rejection and power-loss reset.
Time is supplied explicitly; the intended QEMU adapter must use virtual time.
The nominal 400 ns acquisition plus 1000 ns conversion and cycle multipliers
are model assumptions from the datasheet, not measured hardware timing. Supply
loss is a digital power input, not an analog brownout model. Normal word reads
return the previous sample and schedule a conversion; bit-level bus timing and
short-read conversion behavior still need adapter-level qualification.
The core is now connected to QEMU through `adc101c.c` and
`uconsole-adc101c.patch`, at I2C1 address `0x54` and QOM path
`/machine/battery-adc`. The working-device profile supplies a fixed 3.3 V
reference in QEMU's runtime device tree. `input-uv` and `powered` are writable;
`reference-uv`, `conversion-word`, `alert-level` and `alert-enabled` provide
readback. The alert pin is not routed to an invented carrier GPIO. Voltage
inputs are not yet correlated with the PMIC's battery channel. The builder
hashes/freezes both model sources with the patch set, and Workbench packages
include those sources. The initial revision explicitly blocked migration.
ADC VMState version 1 introduced preservation of registers, pointer/byte position,
read/write latches, held sample, input/reference/power state and virtual-clock
phase/deadline. It includes the I2C parent state so a partial bus transfer resumes
without a synthetic START. The load path validates bounds, reserved register
bits and phase/deadline consistency before scheduling the timer; it restores
alert outputs without starting a new conversion.

`tools/test_emulator_adc_migration.py` passes six real source/destination QEMU
transfers on macOS: acquisition, held conversion, partial I2C write, partial I2C
read, automatic interval with alert/extrema, and power-off/NACK. It is included
in `make check-emulator`. qtest clocks are explicitly aligned on both stopped
instances; this is device VMState evidence, not running-guest or whole-board
migration qualification. Core malformed-state tests also pass with undefined-
behavior instrumentation. Build/transfer logs are retained in
`build/emulator/macos-adc-migration-build.log` and
`build/emulator/macos-adc-migration-qtest.log`. The matching Linux build and all
six transfers now pass too (`linux-adc-migration-build.log`,
`linux-adc-migration-qtest.log`). Both complete emulator gates pass in
`linux-check-emulator-adc-migration.log` and
`macos-check-emulator-adc-migration.log`. Acceptance-harness tests also check
unknown-case rejection and bounded cleanup of only its owned QEMU processes.
Installed-cache and running-guest migration qualification remain open for this
revision; earlier archive hashes do not qualify these newer sources. Other
devices still need their own migration audits. A subsequent PMIC test exposed
one such defect: version-2 VMState saved registers/key state but omitted the I2C
parent, so migration lost the selected slave. The controller could report a
completed write while the PMIC silently ignored the remaining byte. The Linux
counterexample is retained in `linux-pmic-migration-before.log` and
`linux-pmic-migration-before-detail.log` under `build/emulator/`.
`axp2xx-i2c-migration.patch` adds version-3 I2C parent state. Its AXP221 load path
rejects older prototype streams rather than claiming safe restoration; the
previous AXP209 old-stream acceptance policy is unchanged. The new
`test_emulator_pmic_migration.py` checks partial reads/writes, sampled power and
key state, and asserted IRQ restoration. It also accepts a retained version-2
binary to test real legacy-stream rejection. Mac build, both transfer cases,
legacy rejection and ADC migration regression pass; logs are
`macos-pmic-migration-build.log`, `macos-pmic-migration-complete.log` and
`macos-adc-pmic-migration-qtest.log`. The complete Mac emulator gate and all five
migration-harness guard tests pass as recorded in
`macos-check-emulator-pmic-migration.log` and `macos-migration-harness-tests.log`.
Linux now also passes both PMIC transfers, legacy-stream rejection and the
complete emulator gate (`linux-pmic-migration-complete.log`,
`linux-check-emulator-pmic-migration.log`). Full Python suites pass 702 tests on
each host, with 44 explicit macOS skips, recorded in
`linux-python-suite-pmic-migration.log` and
`macos-python-suite-pmic-migration.log`. These runs precede the following replay
journal-reader fix. A fresh MCP power-profile guest regression completed its
replay but exposed a validation bug: dispatch/readback journal entries have an
event index without a completion status. The GUI/MCP acceptance readers now
share a tested selector that distinguishes those intermediate entries from
completed events and run summaries. The failed Mac run is retained in
`build/emulator/pmic-migration-mcp-regression-20260925/` on the qualification host.
Fresh Mac MCP and native Tk runs now pass initial power readings, AC changes,
host-clock replay, cancellation with completed effects preserved and clean
shutdown. MCP additionally verifies guest AC-driver IRQ changes and durable
read-only reconnection. Copied evidence is in
`build/emulator/macos-pmic-migration-mcp-journal-20260925/` and
`build/emulator/macos-pmic-migration-gui-journal-20260925/`. These are ordinary
guest regressions on the new binary, not guest migration tests.
Installed-package qualification remains open for this newer PMIC patch; none
of these results qualify migration of a running full-device guest.
The current full Python suites pass 700 tests on each host (44 explicit
Linux-only skips on macOS); logs are `linux-python-suite-adc-migration.log`
and `macos-python-suite-adc-migration.log` under `build/emulator/`.

`tools/test_emulator_adc101c.py` passes on Linux and macOS through the actual
BCM2835 I2C1 controller, testing virtual-time acquisition/completion, register
access, alert state, power loss, reset and invalid transactions. The first
stock-guest test exposed a controller defect: rejected addresses left TA set,
so the vendor driver could overlook ERR and report phantom full-scale data.
`bcm2835-i2c-nack-completion.patch` completes rejected transfers with ERR/DONE
and clears TA, exposing the error to the driver. ADC and existing PMIC qtests
pass after that correction. The failed guest trial is retained in
`build/emulator/adc101c-guest-20260925/`.

The corrected Linux guest acceptance in
`build/emulator/adc101c-guest-nack-20260925/adc-acceptance.json` passes: raw
0/512/1023 at 0/1.65/3.3 V, scale 3.222656250 mV per count, EIO while unpowered,
recovery after power restoration, clean stop and unchanged base image. The
same native macOS guest acceptance passes in
`build/emulator/macos-adc101c-guest-nack-20260925/adc-acceptance.json`.
Both complete emulator gates pass (`linux-check-emulator-adc101c.log`,
`macos-check-emulator-adc101c.log`). `make package` succeeds on Linux ARM64;
the archive includes both model sources, integration/NACK patches and the
validators. Archive SHA-256:
`7fbb58c9964ba8b8293ee5a73c3c58ab561127671f52770d8162d47a6151d5a4`.
This package inspection does not prove an installed-cache rebuild.
The
full Python suites pass 687 tests on Linux and macOS (44 explicit Linux-only
skips on macOS), recorded in `linux-python-suite-adc101c.log` and
`macos-python-suite-adc101c.log`. These full-suite and archive results predate
the following Forge controls integration.

Forge now routes `adc_input_uv` and `adc_powered` through the shared initial
scenario, live power, GUI and MCP/replay controls. Both model identities and
property types are checked before mutation; ADC input does not alter the PMIC
battery voltage. GUI job/evidence tests pass on Linux and macOS (39 each), as
do scenario, power-evidence and replay tests. The real macOS stdio MCP run in
`build/emulator/macos-adc-mcp-framed-20260925/` proves stock-driver raw readings
0/512/1023, power-off EIO, host-clock replay recovery to 512, clean filesystem
check, unchanged base and durable results after a read-only reconnect.
The failed predecessor is retained in `macos-adc-mcp-failed-20260925/`: a kernel
message interrupted the newline following a successful serial status. Serial
completion now uses an explicit nonce-bound terminator; split/truncated and
wrong-nonce regression cases pass on both hosts. This does not make arbitrary
interleaving within a frame safe; incomplete frames remain uncertain failures.
`tools/validate_adc101c_gui.py` now drives actual Workbench boot, guest-command,
Live power, replay and clean-shutdown controls. The macOS native Tk run passes
the same 0/512/1023, power-loss EIO and replay-to-512 checks, with an unchanged
base and checked stopped root filesystem. Evidence is retained in
`build/emulator/macos-adc-gui-controls-20260925/`. Only history location and the
native file chooser are automated; runtime, controller and guest are real.
The first Linux run stopped before boot because the boot-artifact refresh
exhausted available temporary disk space; its failed evidence is retained in
`build/emulator/adc-gui-controls-20260925/`.
After relocating an inactive generated build to a checksum-verified Mac copy,
the Linux retry passes in `build/emulator/adc-gui-space-recovered-20260925/`.
Both hosts report raw sequences ending in 0/512/1023, three errno-5 reads while
unpowered and recovery to 512. The root check validates the stopped ext4
superblock and clean flag, not a full filesystem scan. Boot refresh now checks
minimum prefix-copy space before the large temporary write, retaining published
artifacts on failure; this is a preflight, not a reservation against other
writers. Its six tests pass on both hosts.
Full-suite evidence is in `build/emulator/linux-python-suite-adc-controls.log`
(695 tests pass) and `macos-python-suite-adc-controls.log` (694 tests pass,
44 explicit skips, before the additional disk-space case). The subsequent
six-test macOS boot-refresh run includes that new case and passes without skips.
Native archive qualification now requires the ADC core/adapter and integration/
NACK patches, checks their hashes, initializes the actual installed MCP launcher
and verifies the advertised ADC field types/bounds, then edits/saves a user copy
through packaged Tk modules without changing bundled source. Linux ARM64 passes
in `build/emulator/native-archive-adc-controls-20260925/` (archive SHA-256
`7bcb0af5bbd372f6cad34734ccd8663bfcdc7beb972df59ecc751cf23ceb2958`).
macOS ARM64 passes in the qualification host's
`build/emulator/native-archive-adc-assets-20260925/` (archive SHA-256
`25fc191041d3f94bc796237b3b6212e2a7b934f4d2207ec2519c336e64127e1d`).
The first Mac package attempt rejected stale firmware provenance; the retry
used the current verified portable MCU firmware and its original Linux build
record. A stale Mac staging script then omitted the model sources, caught by
the new asset check; updating that snapshot to the current packaging script
resolved the failure. Neither failure was waived. These checks do not alone
prove a fresh installed QEMU build or installed GUI-to-guest operation.
The subsequent Mac installed-cache run closes those two checks for that archive:
the packaged builder compiled QEMU 10.2.4 in a fresh isolated user-data tree,
using only packaged patches/model sources and a checksum-checked upstream
archive. The new binary passes the ADC register/timing qtest. Packaged Workbench
modules then pass the real GUI voltage/power/replay/guest checks using that new
binary, with unchanged base and a clean stopped root superblock. Logs and
guest evidence are copied to
`build/emulator/macos-native-archive-adc-assets-20260925/`; the guest's retained
`last-command.json` identifies the isolated installed cache, not the checkout
build. This is not fresh-cache Linux or Linux x86_64 qualification, nor full
device or physical ADC equivalence.
Installed-package acceptance, absent/invalid-reference profiles, migration,
analog timing and physical comparison remain open; this does not complete M3.

Test with QEMU qtests at register/I2C level and guest tests under
`/sys/class/power_supply`, regulator debugfs and interrupt counters. Scenarios
must cover AC insertion/removal, low battery, charging, full, thermal/fault IRQ,
power-key tap/hold and invalid register access.

The sampled PMIC temperature channel now exposes `pmic_temperature_mc` through
the shared scenario/power controls. Its 12-bit registers 0x56/0x57 use the
[Linux 6.12 AXP22x ADC driver's scale/offset](https://github.com/torvalds/linux/blob/v6.12/drivers/iio/adc/axp20x_adc.c):
100 millidegrees C per count and offset -2677. Default input is 25 degrees C;
values quantize downward to 100 mC and span the register's representable range
(-267700..141800 mC), not a claim about the physical chip's operating limits.
Guest writes cannot change the sample; reset preserves it. Register tests cover
endpoints, negative quantization, rejected out-of-range updates, read-only access
and reset. The patch applies to pristine QEMU 10.2.4 and builds successfully.
`build/emulator/pmic-temperature-20260925/` retains qtest and official-guest
evidence: the stock IIO driver reports 25 C, -5.2 C and 85 C through its real
raw/offset/scale attributes. The successful transcript is
`power-client-ef071c0014ab4058ab02b93d553276a2.jsonl`; earlier failures are retained
and identify a validator assumption about an IIO `name` file. The validator now
matches the device-tree compatible instead. Existing AC IRQ, battery and key
checks pass, as do all 654 Linux Python tests. Conversion timing, thermal fault
IRQs/policy, ADC101C and dependent-rail behavior are still incomplete; sampled
temperature does not close the thermal milestone or qualify physical behavior.

An independent boolean `pmic_over_temperature` fault input now exercises the
digital thermal path through shared power controls. The
[AXP221 v1.2 datasheet](https://dl.linux-sunxi.org/AXP/AXP221%20Datasheet%20V1.2%2020130326%20.pdf)
defines status REG01[7], IRQ mask/status REG42[7]/REG4A[7], and optional hardware
shutdown REG8F[2]. The model latches an entry event even while masked, preserves
it until guest W1C, and keeps current fault status separate from the latch.
Reset preserves the injected condition but clears interrupt history. This is
explicit fault injection, not an inferred analogue temperature threshold.
Enabling thermal shutdown during a fault, or injecting a fault while enabled,
ends the VM abruptly; QMP reports non-guest `host-error`, not `guest-shutdown`.
`build/emulator/pmic-thermal-qtest-20260925.log` covers both orderings, disabled
protection, IRQ masking/unmasking, selective W1C, repeated input, reset, read-only
status and active-low GPIO2 propagation, alongside existing PMIC checks.
Production-driver thermal IRQ handling/policy and physical threshold/hysteresis
comparison remain unqualified. The standard Linux 6.12
[battery driver's health property](https://github.com/torvalds/linux/blob/v6.12/drivers/power/supply/axp20x_battery.c)
does not report this die-temperature bit as battery overheat; a battery health
read alone cannot establish thermal acceptance.
The revised patch applies to pristine QEMU 10.2.4; all 655 Linux Python tests
pass (`build/emulator/linux-python-suite-thermal-fault.log`).

The shared controller now durably records single power-change preimages,
dispatch intent and acknowledgement. Tests cover lost acknowledgement, lost
readback, evidence fsync failure before dispatch, and exclusive evidence files;
none retries a mutation or claims rollback. An acknowledged write with failed
readback now produces an explicit uncertain-effect error.
`build/emulator/thermal-controller-20260925-r4/thermal-acceptance.json` qualifies
this against a real disposable maintenance guest: a guest I2C transaction enables
REG8F[2], then the ordinary controller power job injects the fault. The recorded
write is acknowledged before QEMU exits with non-guest `host-error`; readback
fails, the job reports that failure without rollback, and owner inspection says
`exited`. Base checksum is unchanged and `clean_shutdown` is explicitly false.
The dirty overlay, journal and jobs are retained. A separate private QMP observer
is test instrumentation, not a substitute controller: the first attempt occupied
the controller's single-client monitor and timed out before dispatch. The r2
attempt failed in observer setup; r3 passed before the error-message improvement.
Guest register access deliberately uses I2C_SLAVE_FORCE against the bound model
for this test only, not on physical hardware. This does not qualify stock-driver
thermal policy or installed GUI/MCP shutdown behavior. The 662-test Linux suite
passes; the subsequent error wording change passes the 17 focused power/scenario
tests and the repeated real-guest qualification above.

Scheduled power replay now retains the same per-write evidence, indexed by
event, with file/directory fsync before dispatch and no retry after uncertainty.
`build/emulator/thermal-replay-20260925/thermal-acceptance.json` passes a real
three-event scenario through the controller: event 0 changes and verifies the
PMIC sample to 85 C; event 1's thermal-fault write is acknowledged, then the VM
exits with non-guest `host-error` before readback; event 2 is never dispatched.
The terminal replay record says one completed event, failed readback and no
rollback. The underlying base is unchanged; the stopped overlay is retained as
an abrupt-power-loss artifact, not clean-shutdown evidence. The added tests use
the actual power-change implementation to verify event-indexed dispatch/ack
records, preserved earlier effects and suppression of later events. Journal
fsync failure is also tested to block replay before any control operation.
This remains host-monotonic replay, not virtual-time or physical thermal policy
qualification.
All 664 Linux Python tests pass
(`build/emulator/linux-python-suite-replay-evidence.log`).

The same real-guest thermal replay passes on native macOS ARM64 in
`build/emulator/macos-thermal-replay-20260925/thermal-acceptance.json`: the first
event completes, the fault is acknowledged before abrupt non-guest shutdown,
readback fails explicitly, no third event is dispatched and the base is
unchanged. Native QEMU was rebuilt with the revised PMIC patch (binary SHA-256
`b3cc6f48af7443ecceb01195bf321370df0851d540791a9ea6c18271ea7b35bf`).
All 664 Mac Python tests pass with 44 Linux-only skips
(`build/emulator/macos-python-suite-thermal-replay.log`).

The first Mac register test hung during `system_reset` with running qtest dummy
CPUs. Its retained sample (`build/emulator/macos-thermal-qtest-stall.sample`)
shows `pause_all_vcpus` waiting while a dummy CPU remains in `sigwait`. This is
not a verified fix for that running-qtest reset hang. The diskless PMIC tests
now start with `-S`, since they execute no guest instructions; all reset,
temperature, IRQ and shutdown assertions are retained. The three termination
variants pass on Linux and macOS, with Mac output in
`build/emulator/macos-thermal-qtest-paused.log`. The harness now kills only its
exact owned diskless child if TERM cannot stop it, preventing another orphan.
The original failed process was sampled, identity-checked and terminated; no
user VM or disk image was removed. Running-TCG guest replay is independently
qualified above; broader reset/host qualification remains required.

### Stage 4: keyboard deck

Implementation baseline: the compiled descriptor extractor and the host-side
[firmware behavior oracle](keyboard-usb-contract.md) now provide separate byte
and semantic references. The oracle executes the current `.ino` scanner,
keymaps and trackball logic with simulated pins/time; it does not implement USB.
An opt-in `usb-uconsole-keyboard` transport now passes DWC2 DMA descriptor,
report-queue, LED and CDC control/reset-request tests. A firmware-trace encoder
now matches the pinned core's actual report methods and feeds all four report
types through DWC2 DMA. It does not yet connect live host input/LED feedback,
perform bootloader transitions, or replace generic Workbench input devices.
Stock Linux HID/CDC binding and evdev receipt
now pass in a disposable maintenance guest. Desktop integration and hardware
capture comparison below remain open.

Implement a QEMU USB device with the firmware's four HID report collections
(consumer, keyboard, joystick, mouse), USB CDC and the ClockworkPi product,
manufacturer and `20230713` serial strings. Connect host key/pointer input to
matrix positions and direct keys, not directly to Linux keycodes, so the checked
in firmware mapping remains the oracle.

Port the firmware behavior into a small shared/testable state machine:

* 8x8 matrix and 17 direct-key debounce;
* base/Fn layers, Caps/Fn locks and modifier handling;
* brightness, volume and mute consumer reports;
* D-pad/game buttons as either keyboard or 10-bit-axis joystick reports;
* trackball quadrature rate curve, glide/release, Select-to-wheel mode and
  mouse buttons;
* keyboard backlight state.

Then add reset-to-bootloader, disconnect/re-enumeration, DFU identity and upload
failure paths needed by the real flashing scripts. A later fidelity tier can run
the real GD32F103 binary in Renode or another Cortex-M model and bridge its USB
endpoint/GPIO state to QEMU; do this only after the functional USB contract is
covered, because MCU instruction execution alone does not model USB wiring.

Acceptance compares descriptors and report byte streams captured from hardware,
then runs every populated matrix position and direct key through press, hold,
release, Fn and rollover cases. Trackball tests use recorded quadrature traces.

### Stage 5: USB topology and audio

Initial audio launch support is opt-in: `run --audio usb-null`, Workbench's
**Audio surrogate** next-boot selection, or MCP `boot` with `audio: usb-null`.
The default `none` preserves existing launches. The shared controller records
the selection in boot-job provenance. This uses QEMU's USB Audio Interface with
an explicit null backend; it neither opens host speakers/microphones nor models
native carrier audio. The pinned QEMU successfully instantiates it behind its
USB hub. Source inspection confirms this QEMU model is playback-only: capture,
routing/jack scenarios and hardware fidelity remain open.
`tools/test_emulator_audio.py` records this diskless, paused model-construction
check through QMP; `build/emulator/audio-model-20260924.json` passed. It does not
boot Linux or prove that an ALSA stream reaches the surrogate.

`validate_audio_guest.py` now qualifies the Linux path separately in a small
disposable overlay. `build/emulator/audio-guest-20260924/audio-acceptance.json`
passed: USB identity `46f4:0002`, production `snd-usb-audio` binding, one second
of ALSA `S16_LE` stereo playback at 48 kHz, and failure for an absent card.
The guest reported no capture interface. It stopped without forced cleanup;
the primary ext4 superblock was clean with its checksum verified, and the
backing image SHA-256 was unchanged. This is not a full filesystem check,
audible host-output proof or native carrier-audio qualification.

The validator's `--hotplug` run also passed in
`build/emulator/audio-hotplug-20260924/audio-acceptance.json`: QMP removed the
surrogate, Linux confirmed card disappearance, QMP reattached it, Linux confirmed
re-enumeration, and ALSA replayed the tone successfully. Clean-stop superblock
checks and unchanged-base hashing passed again. This exercises surrogate
disconnect/reconnect, not carrier port-power/over-current or native audio rails.

Shared `audio_query` and `audio_set` jobs now inspect or change the owned
surrogate's attachment through QOM/QMP. The readback explicitly reports model,
presence and connection, not guest ALSA readiness or native jack state.
Mutation requires `device-control`; attached read-only clients can query but
cannot inherit the owner's mutation permission. Each operation retains
fsynced request/before/result evidence, and failed readback never claims
rollback. Workbench's **Audio controls** panel uses these same non-cancellable
jobs and disables conflicting local buttons while waiting.

`build/emulator/audio-controls-20260924/audio-acceptance.json` passed the actual
Linux disconnect/reconnect/playback validator through this shared helper, with
durable operation records, clean guest stop and unchanged base. Controller and
GUI permission/dispatch tests cover the adapters. The real Tk panel's buttons
and controller jobs also passed Linux disconnect/reconnect/playback in
`build/emulator/audio-gui-20260924-r2/audio-acceptance.json`, with durable job
history, clean shutdown and unchanged base. This uses the Workbench audio panel
directly, not the full Workbench launch/boot workflow. The first run in
`audio-gui-20260924` failed during disconnect readback with an unexpected QMP
greeting and correctly retained failed evidence without claiming rollback.
The first retry passed with diagnostics only. A diskless hotplug stress probe
then reproduced the cause: a queued `DEVICE_DELETED` event arrived before the
new connection's QMP greeting. The shared client now consumes only asynchronous
events before requiring a greeting, bounded by 64 messages and five seconds;
it does not replay commands or treat a response as a greeting. Malformed input,
EOF, event-flood and deadline tests require rejection before command dispatch.
`build/emulator/audio-qmp-reconnect-20260924.json` records 200 successful hotplug
cycles across 601 connections after the fix. The fresh Linux panel exercise in
`build/emulator/audio-gui-20260924-r3/audio-acceptance.json` also passed with clean
shutdown and unchanged base. The separate stdio MCP process attached to an
owner controller also passed query, disconnect/reconnect and independent Linux
ALSA replay in `build/emulator/audio-mcp-20260924/audio-acceptance.json`. Its
transcript verifies explicit permission denial for guest command execution;
only audio device control was granted. Controller history and operation records
are retained, guest shutdown was clean and the base remained unchanged. This is
not an independent coding-agent exercise. The validator's `--hotplug --gui` and `--hotplug --mcp` options
reproduce the respective frontend exercises.

`validate_audio_workbench.py` separately passed the full maintenance-mode
Workbench launch path in
`build/emulator/audio-workbench-20260924/workbench-audio-acceptance.json`:
select `usb-null`, invoke the actual Start button, verify the boot job's audio
setting, invoke Audio controls, disconnect/reconnect through its real buttons,
check Linux disappearance/reappearance, and replay ALSA audio. The base remained
unchanged and shutdown plus the root superblock checks passed. This is automated
Tk interaction, not a manual UX review, desktop-session audio policy test,
capture or native-audio qualification.

The experimental `usb-wav` launch option now routes playback to a private,
per-launch workspace recording instead of the null sink, through shared
CLI/controller/MCP/Workbench settings. `validate_audio_guest.py --wav` checks
the finalized stereo 48 kHz S16_LE data for duration, non-silence, matching
channels and the generated 440 Hz tone; silence, wrong frequency and truncated
duration fixtures are rejected. The first real run in
`build/emulator/audio-wav-20260924/audio-acceptance.json` **failed**: ALSA returned
success, but the WAV retained only 16,868 of the expected 48,000 frames. The
recording remains evidence of an open USB/audio buffering or timing defect,
not a passed playback-data gate. Hotplug recording continuity and backend
disk-exhaustion behavior also remain unqualified.

Follow-up tracing reproduced USB audio buffer overruns and identified a DWC2
clock-unit bug: the frame counter divided elapsed virtual nanoseconds by a
USB bit-clock interval. `dwc2-frame-clock.patch` now counts negotiated frame
periods and advances the SOF origin by the same elapsed count. The new
`test_emulator_usb_frames.py` gate passes both full-speed 1 ms frames and
high-speed 125 us microframes; evidence is retained in
`build/emulator/usb-frames-both-speeds-20260924.json`. The initial diagnostic
incorrectly assumed a full-speed root port; the final gate reads HPRT speed.
Keyboard remote wakeup still passes with the rebuilt model. The fresh WAV run
in `build/emulator/audio-wav-clock-20260924` improved to 47,472 frames but still
failed the unchanged 48,000-frame minimum. End-of-stream draining remains under
investigation; this timing fix is not yet a passed playback-data gate.

`usb-audio-drain.patch` now keeps the output callback active after alternate
setting zero until accepted device-buffer bytes reach the audio core; it does
not block the USB control request. `make check-emulator` passed after rebuilding.
The new WAV test in `build/emulator/audio-wav-drain-20260924` retained 47,760
frames, still below the unchanged gate. A debug-counter run in
`audio-wav-drain-debug-20260924` reported exactly 191,040 bytes both accepted by
the USB audio model and submitted to the audio core, with no output-overrun
messages. That accounts for all 47,760 recorded stereo frames; the remaining
240 frames were not accepted by the audio model, rather than being lost in the
WAV backend. USB transfer completion and guest draining remain under
investigation. Debug counters are opt-in and the failed evidence is retained.

A disposable guest diagnostic reloaded `snd_usb_audio` with `lowlatency=0`.
It did not fix the tail loss: `audio-wav-lowlatency-off-20260924-r2` contains
864 leading silent frames followed by only 47,760 tone frames. The original
duration/frequency validator incorrectly passed that 48,624-frame file.
`waveform-recheck.json` explicitly supersedes that playback-data verdict;
the original evidence is preserved. The verifier now requires all 48,000
consecutive generated tone samples, allowing constant mixer gain and at most
two sample units of quantization error. A regression fixture rejects the exact
leading-silence/missing-tail pattern. No low-latency workaround was installed
in the image, and stock playback-data qualification remains open.

Guest USB monitoring in `build/emulator/audio-usbmon-summary-20260924` narrowed
the remaining defect further: 167 isochronous submissions and 167 completions
both totaled 191,040 bytes, matching all 47,760 frames accepted by the model.
There were no submission-error records. Thus the missing 240 frames were not
submitted over USB in that run. The next investigation is playback-client and
guest drain behavior, not synthesizing missing samples in the model.
`validate_audio_guest.py --wav --usbmon` now retains compact transfer totals
and boundary records without overflowing the guest-job stdout limit. The first
raw-capture attempt in `audio-usbmon-20260924` exceeded that limit and is retained
as failed diagnostic evidence, not a successful audio acceptance run.

The reproducible `--direct-alsa` client bypasses aplay and records partial-write
counts, negotiated periods, pre-drain delay and drain status. In
`build/emulator/audio-direct-20260924`, libasound accepted all 48,000 frames and
drained successfully, yet USB still submitted/completed only 191,040 bytes.
`--direct-alsa --raw-drain` additionally bypasses the library's drain wrapper
using the Linux hardware PCM drain ioctl; `audio-raw-drain-20260924` reproduced
the same byte count with zero failed USB completions. This rules out aplay and
the library drain wrapper for this fixture; kernel PCM/USB queueing remains
under investigation. Neither diagnostic relaxes the waveform gate. Host null
PCM tests exercise the ctypes signatures and reject truncated input before C
buffer access; they are not guest hardware qualification.

The pinned kernel source exposed the final-request loss: the playback prepare
callback advances the PCM pointer and reports a period boundary before the
new URB is submitted. During drain, PCM core can stop the endpoint there; the
endpoint's post-prepare running-state check then discards that prepared URB.
Candidate `0003-usb-audio-drain-period.patch` defers drain-time period
notification to retirement, letting the existing keep-pending stop path retain
the final submitted request. It does not add synthetic samples or change native
boot settings.

`build/emulator/audio-drain-module-20260925/audio-acceptance.json` passed with
the hash-pinned candidate loaded normally from guest `/tmp`: exactly 48,000
consecutive tone frames, stereo equality, maximum sample error below one unit
after constant gain, and 192,000 USB bytes both submitted and completed across
168 requests, without failed completions. The official guest reports
`6.12.62-v8+`; no force-loading or symbol-check bypass was used. Clean shutdown,
root superblock checks and unchanged base passed. The candidate SHA-256 is
`8e117d8410d3467f12685edab71e6feaef802faaaa527104148599a58bb99bf4` and its bytes
are retained alongside the evidence. This establishes the one-second aplay
drain fixture, not broad ALSA regression, native hardware qualification,
capture, repeated streams or automatic image integration.

Repeated playback is now separately qualified in
`build/emulator/audio-drain-repeat-20260925/audio-acceptance.json` using the same
pinned candidate and `--wav --usbmon --repetitions 3`. Three independent aplay
open/play/drain/close cycles produced exactly 144,000 frames. Each 48,000-frame
window matched the complete reference with maximum sample error below one;
each stream submitted and completed 192,000 bytes in 168 requests with no failed
completions. Clean shutdown, root superblock checks and unchanged base passed.
The repeat-aware verifier rejects incomplete or extra tones and permits backend
silence only outside the matched windows. This extends the fixture evidence to
three successive streams, not arbitrary stream lengths, rates, concurrent
clients, capture, physical audio or automatic installation into user images.

Sample-level hotplug qualification is now available with `--wav --hotplug`.
`build/emulator/audio-drain-hotplug-20260925/audio-acceptance.json` failed:
both the initial and reconnected streams submitted/completed all 192,000 USB
bytes, but the finalized WAV contained only one complete 48,000-frame tone,
not the required two. QEMU's `audio_pcm_hw_gc_out` destroys the unused backend
voice on device removal; `wav_init_out` subsequently opens the same path with
`"wb"`, truncating the earlier stream. This is a host recording lifecycle defect,
not evidence of a guest drain regression. The two-tone check remains strict;
preserving recording data across recreated voices remains required. The failed
run stopped without forced cleanup; its verifier failed before the then-ordered
base/root checks, so it does not claim those checks passed. The validator now
performs those checks before waveform verification to retain independent
filesystem evidence when a sample-level check fails.

`wav-backend-lifetime.patch` fixes that truncation by retaining the file and
cumulative payload length in backend-owned state across device voice removal.
Voice finalization updates and flushes the header and returns to EOF; backend teardown
closes the file. The first voice of a new backend still creates a fresh file.
The patch also bounds cumulative data to RIFF's 32-bit length field; long-file
limits and disk-write failures are not yet acceptance-qualified.
The normal pinned-QEMU builder and `make check-emulator` pass with this patch.
`build/emulator/audio-drain-hotplug-fixed-20260925/audio-acceptance.json` now
passes the previously failing test: both complete 48,000-frame tones remain in
one 96,000-frame recording, with maximum sample error below one and 192,000
USB bytes completed per stream. Guest disappearance/reappearance, clean shutdown,
root superblock checks and unchanged base all passed. Tested QEMU binary SHA-256:
`2a32822c92e25246e12ef5081cdf13d019f39579674a3965337733e4e11a1d2d`.
This tests disconnect between drained streams, not removal during active audio,
wall-clock gap preservation, capture or native hardware audio.

The same sample-level reconnect test now passes through both shared frontends:
`build/emulator/audio-wav-gui-20260925/audio-acceptance.json` invokes the actual
Tk Audio panel buttons, and `build/emulator/audio-wav-mcp-20260925/audio-acceptance.json`
uses a separate stdio MCP process attached to a permission-scoped owner session.
Each retained both complete tones (96,000 frames), with clean shutdown, checked
root superblock and unchanged base. The MCP transcript also checks that a
device-control-only client cannot execute guest commands. These tests use the
hash-pinned candidate USB audio module; they do not make unmodified stock audio
lossless. GUI evidence is automated panel interaction, not manual usability or
the full installed Workbench launch path. `make check-emulator` now includes
diskless audio construction and 200 reconnect cycles (601 QMP connections),
separately from the image-dependent waveform checks. The expanded gate and all
613 Python regression tests passed after this integration.

The WAV backend now flushes sample writes and latches write/flush failures:
failed output is not credited to the cumulative byte count, subsequent writes
return zero, and a replacement voice cannot silently resume the failed
recording. Header creation also checks buffered flush failure. The Linux-only
`tools/test_emulator_wav.py` gate compiles the actual pinned write callback
with isolated clock/type stubs, exercising successful output, `/dev/full`
buffered ENOSPC, failure persistence and the RIFF boundary without allocating
a four-GiB file. Its separate diskless QEMU test confirms a buffered header
failure is logged and shutdown remains clean. These tests now run in
`make check-emulator` on Linux. They do not qualify GUI error presentation,
filesystem recovery, full-process disk exhaustion or all header-finalization
faults.
`build/emulator/audio-wav-write-checked-20260925/audio-acceptance.json` passes
the guest playback/reconnect regression after this change, retaining 96,000
complete frames, with clean shutdown, root superblock checks and unchanged
base. Tested QEMU binary SHA-256:
`83c0865cde97d0a08d3367fc9161a81ba9ca3e21765d224018f1a1284d43b43d`.

Attach keyboard, modem and external devices beneath an explicit carrier hub so
guest paths and hotplug resemble the schematic. Add scenario actions for port
connect/disconnect and power faults.

Initial synthetic capture is implemented by `forge-audio-capture.patch` as a
separate opt-in QEMU device, `usb-forge-capture`. It exposes USB Audio 1.0 stereo
S16_LE at 48 kHz, with an IN endpoint emitting 48 frames per full-speed packet.
Its product string explicitly labels it a synthetic fixture, not a microphone;
it has no host audio backend and is never attached by default. Alternate setting
zero disables capture; stream enable and USB reset reset its deterministic ramp.
Unknown class controls stall, and migration is explicitly unsupported.
The stock official-image `snd-usb-audio` driver enumerated this capture device
and `arecord` returned exactly 48,000 frames matching its integer ramp in
`build/emulator/audio-capture-20260925/capture-acceptance.json`. Both channels,
format, frame count and every sample were checked, with clean shutdown, root
superblock checks and unchanged base. No candidate guest module was loaded.
The tested QEMU binary SHA-256 was
`e5c32acfb42aa7b29fbd59c94ac69a7f527f03c42a3c884e84a94589cd819ef9`.
The 256-frame repeating pattern detects deviations from that pattern, not
loss/repetition of an exact whole pattern; stronger sequence fixtures remain
desirable. This establishes the USB capture application path, not host microphone
access, native analog circuitry, real-time accuracy, all USB fault cases or
simultaneous playback/capture. `make check-emulator` includes
its diskless construction check; the image-dependent validator is:

```sh
python3 tools/validate_capture_guest.py --image PATH --sha256 EXPECTED_SHA256 --output NEW_DIRECTORY
```

The synthetic input is now integrated as `usb-capture` through CLI launch,
Workbench's Audio selector, controller boot and MCP boot schema. Shared audio
query/connect/disconnect jobs validate the capture-specific QOM type and recreate
`usb-forge-capture` without any `audiodev` property. Readback distinguishes capture
from playback and explicitly reports no host microphone. The default remains
`none`. The later `usb-duplex` integration below adds simultaneous I/O.
`build/emulator/audio-capture-gui-20260925/capture-acceptance.json` and
`build/emulator/audio-capture-mcp-20260925/capture-acceptance.json` both pass
48,000-frame pattern checks before and after disconnect/reconnect, including
independent guest removal/reappearance, clean shutdown and unchanged base.
The GUI test invokes the real panel buttons; the MCP test uses a separate stdio
process with device-control permission and verifies guest execution is denied.
These are panel/attached-client tests, not full installed Workbench launch or
independent coding-agent task qualification. Reproduce with `--hotplug --gui`
(under a display) or `--hotplug --mcp` on the command above.

The initial repeating-ramp limitation is now addressed by sequence fixture
`stereo-u32-frame-counter-v1`: each stereo S16_LE frame carries the low and high
16-bit halves of a monotonically increasing 32-bit counter. This is diagnostic
PCM, not a listening tone. `capture_pattern.verify_capture` is both unit-tested
on the host and uploaded unchanged to the guest; it verifies exact byte count
and every counter step, allowing an arbitrary initial sequence and uint32 wrap.
Tests reject silence, channel swaps, truncation, and one- or 256-frame sequence
loss/repetition. The new pattern repeats after 2^32 frames rather than 256.
`build/emulator/audio-capture-sequence-20260925/capture-acceptance.json` passes
48,000-frame sequence verification before and after attached-MCP hotplug, with
clean shutdown, checked root superblock and unchanged base. Tested QEMU SHA-256:
`72be09a37a82c05a410f5985de14e18edb86f12cd5d8f362cdcda2176410a04c`.
Earlier ramp evidence remains historical, not evidence for this new data pattern.

The complete Workbench maintenance-mode launch path now passes the same
sequence checks in
`build/emulator/audio-capture-workbench-20260925/workbench-audio-acceptance.json`.
`validate_audio_workbench.py --capture` selects the real read-only Audio
combobox entry, invokes Start, checks the submitted boot mode, opens Audio
controls, and uses its disconnect/reconnect buttons. Independent guest checks
record sequences 0..47999 both before and after hotplug. Clean shutdown, checked
root superblock and unchanged base pass. Selection tests reject absent options
and ambiguous widgets rather than setting an unsupported backing variable.
This is automated source-checkout Workbench interaction, not installed-package
host-matrix coverage, manual usability or desktop capture policy qualification.

A fresh Linux ARM64 package also built after this integration:
`build/uconsole-workbench-linux-aarch64.tar.gz`, SHA-256
`6198cd0b7104f16d0d65a7f3e0342e95e82b456dc4e3b69e471127e7d1cc42c8`.
The build regenerated keyboard firmware after its provenance check detected the
changed build recipe; the archive includes that firmware, capture verifier and
capture QEMU patch. Actual extracted-archive launch outside the checkout and
user-owned source edit/save passed, with bundled source unchanged. The initial
installed validation attempt failed because its old fixed click coordinates
targeted a toolbar after layout changes. The validator now locates the unique
source editor via read-only Tk widget geometry queries on its private test X
display, then exercises ordinary mouse/keyboard editing and save. This corrected
test passed against the same archive. It is installed-launch/source-edit proof,
not installed capture boot or cross-platform release qualification.

Installed capture boot is now separately verified against that same archive:
`build/emulator/audio-capture-installed-20260925/installed-capture-acceptance.json`.
`validate_installed_capture.py` extracts the archive, launches its actual
Workbench and MCP wrappers outside the checkout with isolated XDG user data,
and drives a GUI-owned maintenance VM through the private attached-client
interface. Both initial and reconnected captures verified sequences 0..47999;
clean stop, root superblock checks and unchanged base passed, and no owner
process was retained. Packaged tool execution is proven here, not installed
QEMU compilation: the isolated cache links the already-built QEMU binary with
SHA-256 `72be09a37a82c05a410f5985de14e18edb86f12cd5d8f362cdcda2176410a04c`.
The test retains extracted files, GUI log, MCP transcript, durable jobs and VM
evidence. New preflight guards reject missing archive/QEMU inputs before
creating a fixture; uncertain shutdown retains the owner and its attachment
socket for diagnosis instead of killing a potentially live guest.

Native macOS ARM64 packaging was exercised on `puck.local` in an isolated
`/tmp/uconsole-qualification.XYYbji` source snapshot, with Bash 3.2 and Clang.
This exposed an interpreter-selection gap: Apple's Python 3.9 supplied Tk but
cannot run current forge code. Launchers and build entry points now enforce
Python 3.12+; GUI discovery also probes Homebrew's versioned 3.12 interpreter.
The version check remains effective with `PYTHONOPTIMIZE=1`. Homebrew
`python-tk@3.12` and its Tcl/Tk dependencies were installed for qualification;
no existing source checkout was modified.
`build/emulator/macos-package-20260925/native-archive-acceptance-r3.json` passes
both extracted launchers, packaged MCP initialization, and native Tk construction
plus user-owned edit/save with an unchanged bundle. The preferred interpreter
was deliberately `/usr/bin/python3`; fallback used Python 3.12.14/Tk 9.0.4.
The retained archive `uconsole-workbench-macos-arm64-r3.tar.gz` in that directory
has SHA-256 `fd0de958db2f7ea4b5b644a960a72d8f76c4348e6db42a66d76687890d9b22d6`.
Native helper/oracle binaries are ARM64 Mach-O and all 13 packaging/flashing
tests passed on macOS. This is native package/Tk evidence, not macOS QEMU guest
boot, manual GUI usability, physical flashing or the final release revision.

Pinned QEMU 10.2.4 now builds natively on macOS ARM64 with the current patch
set. Qualification exposed and fixed two build portability issues: safe tar
extraction rejects the upstream EDK2 `X11IncludeHack` absolute link, and macOS
patch rejects the capture registration hunk's insufficient context. The builder
now omits only that exact unused link to `/opt/X11/include`, keeps `data_filter`
for all other entries, and publishes an extracted source directory only after
successful temporary-directory extraction. Tests on Linux and macOS reject
other absolute/escaping links without leaving a partial source directory.
Patch application is explicitly noninteractive and the capture hunk now has
full surrounding context. The old failed Mac extraction was preserved as
`qemu-10.2.4.incomplete-20260925`, not silently reused.
The native binary at `/tmp/uconsole-qualification.XYYbji/build/emulator/qemu-build/`
has SHA-256 `a03aa573d4755276d18772043c4dd7962f743b4ce38e5bf65ed670fe4804c522`.
`make check-emulator` passed on macOS after fixing a test assertion to resolve
both sides of macOS's `/var` versus `/private/var` alias. This includes watchdog,
PMIC, GPIO, GIC, USB frame/wakeup, keyboard DMA, audio construction and 200 QMP
reconnect cycles. The Linux-only `/dev/full` test is not run on macOS. All 634
Python tests also passed on Linux at that revision.

Native macOS ARM64 maintenance boot and synthetic USB capture now pass with
the same pinned QEMU binary above and base image SHA-256
`a7b0a2bfa86a45150af1ae70a94ed432718bfec90ffdef54952564bd34f0d788`.
`build/emulator/macos-capture-20260925/capture-acceptance.json` records stock
Linux `snd-usb-audio` capture of 48,000 exact sequential stereo frames before
and after an attached stdio-MCP disconnect/reconnect. Linux independently
confirmed removal; clean shutdown, the ext4 superblock check and unchanged
base checksum passed without forced cleanup. Logs, MCP transcript and job
history are retained locally; the disposable overlay remains on the Mac.
This is a source-tree maintenance guest test, not native analog audio, desktop
usability, an installed-archive guest test or final release qualification.

The first full macOS Python run (634 tests) exposed 9 failures and 97 errors,
with 39 skips. History now canonicalizes workspace paths at its API boundary;
alias tests also check that retargeting an alias cannot expose another
workspace's records. Host cancellation keeps its process-group leader unreaped
until the last signal. On Darwin, an EPERM is accepted only after a successful
process census proves the leader and every remaining group member are zombies;
live members, failed census and non-Darwin permission failures remain errors.
The 5 signal guard, 14 history and 10 host-task tests pass on both hosts,
including a TERM-ignoring descendant. Canonical-path fixture fixes and a
narrower image-worker mock bring all 98 focused macOS GUI/MCP/project/client/
keyboard tests to green. The full Linux suite passes all 640 tests under Xvfb
after these fixes. Linux-target worker tests that assume `/proc`,
`/etc/machine-id`, Linux xattrs and `O_NOATIME` still require explicit separation
from portable host tests. This initial run did not qualify the full macOS suite.

The follow-up macOS run (`macos-python-suite-r2.log`, retained under
`build/emulator/`) reduced this to 90 errors, all in tests using local Linux
target identity/filesystem facilities. Portable planning, protocol and backup
verification tests now use explicit target records; tests executing real Linux
workers remain mandatory on Linux and are explicitly identified on macOS.
The 197 target tests pass on Linux without skips. The full 640-test Linux suite
also passes after this separation. macOS r3 passes with 78 skips, but this is
not the final host gate: 34 GUI cases were incorrectly excluded by X11-only
`DISPLAY` checks. Native Tk is now enabled for those cases; the focused group
of 42 GUI/audio/modem tests passes. Full r4 then exposed a reproducible Tk 9.0.4
`showRootWindow` crash after a widget fixture destroyed its root before its
initial idle callback ran, consistent with the deferred callback in
[Tk 9.0.4 initialization](https://github.com/tcltk/tk/blob/core-9-0-4/macosx/tkMacOSXInit.c).
Both the failure and targeted reproduction logs are
retained. The fixture now drains idle initialization before destroying its root;
the 41-test widget/Workbench reproduction now passes. An intervening attempt
stalled in AppKit's post-crash window-restoration alert, confirmed by the retained
`macos-tk-idle-stall.sample`. Only that owned test process was terminated. The
successful reproduction used process-local `-ApplePersistenceIgnoreState YES`
arguments (not a persistent defaults change), with an explicit `unittest.main`
argument list. The full native r5 run with that setup passes: 640 tests,
44 explicit Linux-only skips, including the target-worker, guest-supervisor and
libasound checks. Its verbose log is retained as `macos-python-suite-r5.log`.
This establishes the current source-tree host test gate, not final archive or
whole-plan release readiness.

The macOS source-tree Workbench also passes real maintenance guest boot and
synthetic capture through its audio dropdown, Start and Audio controls buttons.
`build/emulator/macos-workbench-capture-20260925/workbench-audio-acceptance.json`
retains the boot job, exact capture checks before/after hotplug, clean root
superblock and unchanged base evidence. No host microphone was used.

Live host/target qualification from macOS passes both Workbench author/approve/
apply/restore and stdio-MCP apply/restore over existing SSH to
`jkh@clockworkpi.local`. Evidence is retained in
`build/emulator/macos-target-gui-author-20260925/` and
`build/emulator/macos-target-mcp-20260925/`, including private host-side backups,
plans, authorization, jobs and fresh restored-state captures. Each run deploys
the same hash-pinned inert application from the prior forge image exercise,
executes it, then verifies its original absence. Neither run flashes a card,
changes boot configuration nor installs a target agent. A fresh read-only check
after both runs reports the expected machine identity and running system; native
`config.txt` and `cmdline.txt` hashes match the earlier physical reboot evidence.
This qualifies bounded live application recovery from the Mac, not arbitrary
package/kernel recovery or physical boot of an entire exported image.

The opt-in `usb-duplex` mode now attaches both WAV-backed playback
(`audio-surrogate`) and synthetic capture (`audio-capture`). CLI, Workbench,
controller and MCP share the mode set; no host microphone or speakers are used.
Queries retain per-device presence/attachment. Aggregate flags require both
devices; hotplug changes each device sequentially and preserves partial-failure
evidence without claiming atomicity or rollback. A subsequent explicit request
reconciles the observed state without recreating an already connected peer.
Unit tests cover second-device failures, wrong QOM identities, partial-state
reconciliation, private recording allocation and schema agreement.

`build/emulator/audio-duplex-20260925/audio-acceptance.json` passes a real Linux
guest concurrent `aplay`/`arecord` probe before and after attached MCP hotplug.
Both ALSA status files reported RUNNING during playback. Each three-second
capture contains exactly 144,000 sequential stereo counter frames. The finalized
WAV contains both complete 48,000-frame tones (96,000 frames total), verified
sample-by-sample with constant mixer gain/quantization tolerance. Linux confirms
both devices absent/reappearing; clean stop, root superblock and base checksum
checks pass. This test explicitly loads only the hash-pinned candidate
`snd-usb-audio.ko` drain fix into the disposable guest. It is not automatic
installation or proof of lossless stock-driver playback, native duplex hardware,
host real-time scheduling, or installed-package/host-matrix completion.

The equivalent concurrent test also passes on macOS ARM64 in
`build/emulator/macos-audio-duplex-20260925/audio-acceptance.json`, using the
same guest/module hashes and pinned native QEMU. The Linux Tk audio-panel
buttons pass the same before/after data checks in
`build/emulator/audio-duplex-gui-20260925/audio-acceptance.json`. These are
shared-controller frontend tests; installed Workbench duplex launch is qualified
separately below. The Linux suite now passes 653 tests. The companion audio skill
reference now explains the two-card mode, concurrent-data evidence, private WAV
handling and partial hotplug recovery; its examples validate against MCP schemas.
Per-device dispatch/acknowledgement records additionally distinguish a completed
first command from an unacknowledged second command; focused failure tests pass.
The corresponding native macOS full suite also passes 649 tests with 44
explicit Linux-only skips (`build/emulator/macos-python-suite-r6.log`).

Installed Linux ARM64 duplex qualification now passes in
`build/emulator/installed-duplex-20260925/installed-capture-acceptance.json`.
The tested archive SHA-256 is
`addba2d9ac6674dc99aabe076acd453b2242f40af8e8114595cfa9060d13fd5f`.
Its extracted Workbench launcher owns the VM; its extracted MCP launcher drives
boot, transfers, execution and both-device hotplug outside the checkout, with
isolated XDG data/state and no inherited Python/resource-root overrides.
Both concurrent probes verify 144,000 capture frames and overlapping ALSA
RUNNING states; the private finalized WAV verifies two complete 48,000-frame
tones. Both USB identities disappear and return. Clean shutdown, the root
superblock check and unchanged backing-image hash pass; no owner is retained.
The same archive passes launcher/MCP initialization and native Tk source
edit/save with an unchanged bundled source in
`build/emulator/native-archive-duplex-20260925/native-archive-acceptance.json`.
This uses the existing pinned QEMU cache and explicitly uploads the pinned
candidate drain-fix module only into the disposable guest. It does not qualify
installed QEMU compilation, automatic driver integration, native hardware audio,
or the complete installed host matrix.

Installed macOS ARM64 duplex qualification also passes in
`build/emulator/macos-installed-duplex-20260925-r3/installed-capture-acceptance.json`.
The retained native archive in that directory has SHA-256
`c1f8da21651b0364391b55da5d0580106846fde877cf4bf3cab0d9662e2f5c15`.
Both packaged launchers, GUI-owned maintenance boot, pinned-module transfer,
concurrent data verification before/after hotplug, private 96,000-frame WAV,
clean stopped root superblock and unchanged base pass; no owner is retained.
This has the same prebuilt-QEMU/candidate-driver scope limits as the Linux run.
Initial GUI startup attempts timed out before boot: the retained r2 process
sample shows AppKit's `promptToIgnorePersistentState` modal, not a guest failure.
Workbench now accepts the native macOS `-ApplePersistenceIgnoreState YES|NO`
option without consuming it from `sys.argv` before Tk initialization. The r3
qualification explicitly opts into YES, recorded in its evidence. No persistent
defaults or saved-window files were edited. Tests cover accepted native values,
invalid values and rejection on other platforms. Linux's full 653-test suite
passes (`build/emulator/linux-python-suite-r7.log`).
The native Mac suite also passes 653 tests with 44 Linux-only skips
(`build/emulator/macos-python-suite-r8.log`). Its preceding r7 run is retained:
the invocation omitted the Python 3.12 build PATH and failed the package-build
prerequisite, with three additional dependency skips. The r8 invocation restores
the documented Homebrew PATH rather than weakening that prerequisite.

For application work, attach a QEMU USB audio device to a host null/file/real
backend and model GPIO10 jack insertion plus GPIO11 speaker enable. Verify ALSA
enumeration, playback/capture data, mute/routing and jack events. The fidelity
tier then implements the actual BCM2835 firmware/PWM audio path feeding the
carrier filters and AW8110 enables; it must not claim the USB surrogate validates
analog audio, noise, gain or jack circuitry.

### Stage 6: SIM7600 extension

The AT-engine foundation now exists in `tools/forge_modem_at.py`: bounded serial
framing, per-port echo/error settings and subscriptions, shared synthetic SIM
PIN/PUK state, CFUN radio state, CSQ and basic CREG/CGREG/CEREG reporting. Nine
tests pass, including fragmented input, oversized-line rejection, cross-port
state, lockout and an actual socket exchange/disconnect. Unsupported commands
return errors rather than fabricated success. `tools/serve_modem_at.py` can
attach this engine to the existing loopback serial surrogate; this does not
replace the composite USB milestone, provide packet networking, or establish
ModemManager/hardware compatibility. Controller-owned lifecycle, state-change
notification scheduling and guest acceptance remain open. Command formats use
SIMCom's SIM7500/SIM7600 AT manual V3.00, with a deliberately documented subset.

Guest serial acceptance now passes through QEMU's real generic FTDI device,
not merely a socketpair. A diskless RAM guest loaded matching usbserial/ftdi_sio
modules into `/run`, then `validate_modem_at_guest.py` compared an independently
specified byte-exact transcript covering echo disable, numeric errors, locked
SIM, PIN unlock, registration, radio-off and unsupported BOOTLDR rejection.
Evidence and module hashes are retained in
`build/emulator/modem-at-guest-20260925/`. The recovery image was not modified;
the physical target was read only to obtain the two matching modules. The guest
was shut down and both QEMU and the AT adapter exited successfully. Ten AT unit
tests pass, including rejection of embedded-newline command splicing. This is
still FTDI-surrogate transport evidence, not composite USB/ModemManager or
packet-network acceptance.

The AT engine now has a shared `Modem` coordinator for primary/secondary AT
ports. Commands and validated scenario changes emit ordered registration URCs
to each subscribed port, with independent echo/error/subscription settings.
Off/on transitions within one serial chunk are both retained, unchanged states
do not emit duplicate URCs, and invalid scenario updates are atomic. The
loopback adapter now uses this coordinator; socket acceptance covers an actual
unsolicited registration notification. All 33 focused modem tests pass. This
does not yet attach the second channel to USB or expose scenario control through
the controller; those integrations remain part of the composite-device work.

`Code/patch/qemu/forge-modem.c` now contains a five-interface USB serial
prototype, with independent full/high-speed bulk endpoints, per-interface
control-line state, bounded receive/transmit queues, nonblocking chardev writes
and disconnect/reset cleanup. Primary/secondary backends occupy interfaces 2/3;
the product string explicitly says serial prototype with no network. Migration
is blocked rather than silently discarding the external AT engine state. It
passes a syntax-only compile using the existing pinned QEMU compile command and
headers, but is not yet in the builder's model inputs or a runtime binary.
Build integration, option-driver enumeration, backpressure/reset tests and
network interfaces remain open. The physical uConsole currently enumerates
only the carrier hub and keyboard, not a modem, so no new physical modem
descriptor-equivalence evidence was obtained.

The model is now included in the builder's frozen source inputs and registered
through `forge-modem.patch`. Because host disk space is limited, its first build
uses isolated `/dev/shm/uconsole-modem-build.4VYSoa`, with two compile jobs; the
normal `build/emulator/qemu-build` symlink remains unchanged. The loopback AT
adapter now accepts `--secondary-port` and multiplexes both channels through a
single shared engine, with 64 KiB bounded per-channel output and EOF draining.
All 34 focused modem tests and nine builder tests pass; a two-socket test proves
cross-channel notification, retained shared state after one channel disconnects,
and pending-response delivery on half-close. Matching option/usb_wwan/usbserial
modules were copied read-only from the physical target into
`build/emulator/modem-composite-guest-20260925/` for diskless guest enumeration.

The isolated build completed successfully (generation
`f1ea7a0866f3731e0e3d3652300222a15e1e4c5db56e47f1d1ebfaff97dda510`).
QEMU instantiated `usb-forge-modem` behind xHCI and exited cleanly via QMP;
this smoke check is not guest enumeration evidence. Build log:
`build/emulator/qemu-modem-build-20260925.log`. The candidate remains in tmpfs,
not a durable release artifact or a replacement for the normal emulator build.

The candidate now passes diskless guest composite-serial acceptance. Linux's
option/usb_wwan stack binds all five interfaces on a single USB device, in role
order 0..4. A byte-exact transcript proves PIN query/unlock on interface 2,
registration notification on interface 3, and shared radio state changed from
interface 3 then queried from interface 2. Evidence is retained at
`build/emulator/modem-composite-guest-20260925/guest-r3/`, alongside the earlier
failed validator attempts (incorrect `option` versus actual `option1` label;
unsupported physical baud-rate setting). The corrected validator checks raw
tty operation, exact roles, shared USB ancestry and module fixture hashes.
All 36 focused modem tests pass; retained enumeration evidence also passes the
strengthened checker. QEMU and the two-channel adapter exited cleanly after
guest reboot, with all three test listeners gone. This qualifies the five-port
serial prototype only: RNDIS/QMI, ModemManager discovery, full USB control/reset
behavior, physical descriptor matching and complete checkpoint state remain.

Replace the current single `usb-serial` substitute with one composite device:

* five serial functions matching ignored, GNSS, primary AT, secondary AT and
  audio roles;
* RNDIS first, followed by optional QMI/`cdc-wdm` mode;
* deterministic AT engine for SIM PIN, registration, operator, signal, APN,
  PDP context, SMS, calls, GNSS and identity/firmware queries;
* GPIO24 power and GPIO15 PWRKEY timing, RESET and NETLIGHT state;
* `AT+CUSBPIDSWITCH` disconnect/re-enumeration and `AT+BOOTLDR` transition;
* fastboot identity, partition list and safe scratch writes suitable for testing
  the updater without distributing or modifying modem firmware;
* fault scenarios: absent SIM, locked SIM, no service, roaming, weak signal,
  dropped registration, USB reset and interrupted update.

Back networking with QEMU user-mode NAT; never imply RF/baseband simulation.
Add the NAU8810 I2C/PCM path only after the USB/ModemManager contract passes.

Acceptance runs ModemManager discovery, AT/GNSS transcripts, NetworkManager
activation and updater preflight against both recorded hardware transcripts and
the model. Destructive fastboot tests use synthetic partitions only.

### Stage 7: radio, HDMI, storage and expansion

Keep USB networking and host HCI as declared surrogates until a test requires
CM4 radio-driver fidelity. If that threshold is reached, model SDIO enumeration,
brcmfmac firmware/NVRAM loading, UART Bluetooth HCI, rfkill and suspend/wake;
actual RF propagation remains out of scope.

Add EDID/HPD/CEC scenarios for HDMI, SD removal/write-protect/error injection,
and socket-backed SPI/I2C/UART peripherals. Camera CSI requires a separate named
sensor/ISP contract and should not be faked as a permanently successful node.

### Stage 8: physical qualification and upstreaming

For each device, collect a minimal, non-secret hardware trace: device tree,
enumeration, driver bind, register/USB transaction subset, sysfs state and event
sequence. Replay the same scenario in QEMU and compare ordered state changes.

Keep each QEMU device patch independently reviewable with qtests and migration
state. Submit generic BCM2711/AXP/SIM7600 improvements upstream where possible;
keep only the uConsole machine composition and scenario assets in this repository.

## Completion matrix

A subsystem is complete only when all applicable cells are backed by evidence:

| Gate | Required evidence |
| --- | --- |
| Static topology | Schematic and DT mapping agree; revision/population is identified |
| Enumeration | Production guest driver binds with expected IDs and interfaces |
| Happy path | Real application or system service completes its workflow |
| State changes | QMP/scenario input produces the expected guest event and queryable state |
| Failure path | Missing device, bad command, timeout and interruption are not reported as success |
| Persistence | Reset, power cycle and migration behavior are defined and tested |
| Physical comparison | Sanitized hardware and emulator captures are compared, with known differences documented |
| UI/tooling | Workbench exposes safe controls and accurately labels fidelity |

The whole-device goal is not satisfied by a booting image or a green unit suite:
the display, power, keyboard, USB, audio, modem, radio, storage and expansion
rows must each have an explicit achieved level and unresolved differences.

### Composite modem RNDIS prototype evidence (2026-09-25)

The `usb-forge-modem` prototype now subclasses QEMU USB networking, retaining
five option-driver serial interfaces and adding RNDIS control/data interfaces
5/6 in configuration 2. Serial endpoints are 3–7; network endpoints are 1/2.
The synthetic descriptor layout is not a captured physical SIM7600 contract.

The isolated QEMU build passed. Diskless CM4 guest evidence is retained under
`build/emulator/modem-composite-guest-20260925/guest-rndis-serial/` and
`guest-rndis-network-r2/`: five serial roles, shared PIN/radio state and
cross-port notifications passed; Linux `rndis_host` bound to interface 5 and
a 262144-byte local HTTP payload crossed the separate modem subnet with SHA256
`2312394bd99545d9de131c24efb781e765ac1aec243f2ed9347597a793a415e9`.
The first network validator attempt encountered an already-loaded module;
the corrected validator permits that diskless fixture state. Its recorded
module digest identifies the supplied fixture, not independently the loaded
kernel module. Neither test wrote to the physical target or attached a disk.

This is USB transport evidence only. Radio/registration gating of networking,
PDP contexts, fault injection, reset/hotplug behavior, shared-state migration,
physical descriptor comparison and Workbench integration remain open M5 gates.

Radio gating follow-up: the AT coordinator now synchronizes registered/home
or roaming state to an explicit QMP NIC link callback before returning command
success. Failed updates poison the coordinator rather than silently retrying.
The standalone adapter accepts an explicit local QMP port and modem device ID;
this is not yet shared-controller integration. Forty-two modem unit tests pass.
The diskless `guest-gate-serial/` acceptance passed, but subsequent radio control
in `guest-gate-network/` and `guest-gate-network-r2/` timed out after reopening
the serial port. Radio-gated packet transport is therefore **not qualified**.
The VM was shut down; the adapter exited with an error when its final link-down
request encountered the stopped QMP endpoint. Reopen behavior and shutdown
coordination both require follow-up before this path is release-ready.

Follow-up evidence resolves the radio-toggle test failure: socket tracing
confirmed commands and replies at the adapter. Configuring termios through the
same open descriptor used for AT traffic avoids the separate setup close/open
boundary. `guest-trace-network-r4/` and `guest-trace-network-r5/` both pass the
262144-byte transfer, radio-off ping with 100% packet loss, and restored ping
after radio-on. Earlier failures remain retained, including remote exit status.
This qualifies radio toggling only; registration-fault and PDP behavior remain
open. The CLI now handles SIGTERM by completing its link-down cleanup before
exit. The updated adapter exited 0 while QEMU was live, followed by a guest
reboot and QEMU exit 0. The prototype owner must stop the adapter before QEMU;
unexpected monitor loss is still reported as failure, not successful cleanup.

Registration scenario plumbing now accepts bounded newline-delimited JSON on a
caller-supplied private socketpair, multiplexed with the AT ports by the same
event loop. Requests contain an increasing `sequence` and validated `changes`;
acknowledgements report observed state only after the link callback completes.
Duplicate fields, replayed sequences, oversized/truncated frames and invalid
state are rejected. PIN contents are omitted from acknowledgements. Socketpair
tests prove registration-denied updates, AT queries, URCs and link callbacks
share state; focused modem coverage is 53 passing tests. This is internal
transport plumbing, not yet a Workbench/MCP operation or guest packet-loss
qualification for registration faults. Those integration gates remain open.

Registration-fault guest qualification now passes in
`build/emulator/modem-composite-guest-20260925/guest-registration/`.
The private scenario socket drove seven transitions: denied, roaming,
searching, unregistered, home registration, SIM absent and SIM ready.
Every acknowledgement was followed by an exact guest `AT+CEREG?` transcript
and an interface-bound ping: unavailable service produced 100% packet loss,
and home/roaming service restored traffic. The same run repeated composite
serial, payload-transfer and radio-toggle acceptance first. Adapter shutdown
recorded stopped=true with no errors while QEMU remained alive; the subsequent
guest reboot ended QEMU with exit 0. Focused modem coverage is 56 passing tests.
This qualifies synthetic registration/SIM packet gating, not PDP contexts,
physical radio behavior, or Workbench/MCP ownership integration.

Managed runtime integration is now implemented behind `run --managed --modem
composite`. QEMU modem AT channels use private Unix sockets inside the owned
runtime directory; the worker uses a private scenario socketpair and the
runtime's VM-identity-checked QMP control, not arbitrary public monitor ports.
Startup pauses the VM until modem initialization completes. Clean shutdown
stops the worker before QEMU; explicit forced stop still terminates the owned
child if modem cleanup reports an uncertain result. Focused coverage passes
58 modem tests and 13 runtime tests, including private endpoint construction,
scenario dispatch, paused initialization and shutdown ordering. This new
managed path still requires live runtime qualification and controller/UI/MCP
exposure. The earlier full-suite run passed 832 tests in 86.523 seconds before
these latest runtime edits (`full-tests-modem-registration-20260925.log`).

Controller/MCP integration now exposes composite modem selection on boot and
permission-scoped `modem_set` jobs. Requests validate before submission,
snapshot values, serialize with other workspace jobs, and fsync per-operation
dispatch/outcome evidence. PIN values are not accepted by this public surface.
Workbench has a next-boot modem selector using the same boot job. Tests pass:
35 MCP tests (including denied grants, invalid fields, dispatch-before-mutation
and uncertain-result/no-retry evidence) and 41 Workbench GUI tests, including
selection snapshotting. A dedicated modem control panel and live end-to-end
managed-runtime/client qualification remain open; these tests do not replace
the earlier diskless guest evidence or prove physical modem equivalence.

The dedicated Workbench modem panel is now implemented using `submit_modem`.
It exposes one typed field/value request at a time, distinguishes requests from
observed acknowledgements, honors device-control permissions, disables duplicate
submission, and retains pending jobs across status-transport errors. Manual
status rechecking never resubmits a mutation. Pending jobs block panel and
Workbench closure. The 63 focused modem tests pass under Xvfb, including five
panel interaction tests; live owned-runtime GUI/agent qualification remains open.

Live managed Workbench modem qualification passes in
`build/emulator/modem-workbench-20260925/modem-workbench-acceptance.json`.
The actual Start button booted a disposable overlay with the composite modem;
the modem panel submitted registration-denied, then the MCP server handler
submitted roaming against the same controller/runtime. Native guest Python
opened `/dev/ttyUSB2`, configured raw mode through that descriptor, and verified
exact `AT+CEREG?` replies after both changes. Both jobs retained acknowledged
evidence and boot history retained the composite selection. Clean shutdown
exited QEMU 0 without forced cleanup; the ext4 superblock was clean with its
checksum checked, and the base image SHA256 was unchanged. This is not a full
filesystem check. The MCP leg used the in-process protocol handler, not an
independent external agent process; cross-client/external-agent qualification
remains open. The reproducible driver is `tools/validate_modem_workbench.py`.

The same driver with `--external-client` now qualifies a separate stdio MCP
process connected through Workbench's private attachment socket. Evidence is
retained in `build/emulator/modem-external-20260925/`. It confirmed exactly the
owner-granted device-control scope, denial of boot without a grant, successful
`modem_set` and job polling, and process exit 0. A native guest AT query then
verified roaming, and the runtime object remained the same GUI-owned instance.
This exposed and fixed a missing `modem_set` entry in the attachment permission
map; eight scoped-client tests pass, including explicit grant/workspace checks.
Guest shutdown remained clean with no forced cleanup, clean checked ext4
superblock, and unchanged base hash. This is external MCP transport evidence,
not completion of the broader independent-agent edit/test/export milestone or
physical modem/PDP equivalence.

Read-only modem queries now traverse the same private worker protocol and are
exposed through controller/MCP `modem_query` and Workbench's Query state button.
They do not require a mutation grant, emit URCs, alter partial AT commands, or
write the link state. Uncertain workers fail rather than laundering cached
state into verified observations. Unit coverage passes 67 modem tests and
eight scoped-client tests. Live external-client query evidence in
`build/emulator/modem-query-20260925/` reports observed roaming after the
acknowledged change and agrees with the subsequent native guest AT query.
Shutdown was clean without forced cleanup, and the base hash remained unchanged.

An initial synthetic IPv4 PDP subset is now implemented from the SIMCom
[SIM7500/SIM7600 AT manual V3, sections 8.2.2–8.2.4](https://files.waveshare.com/wiki/SIM7600G-H/SIM7500_SIM7600_Series_AT_Command_Manual_V3.00.pdf):
`CGDCONT` definition/query, `CGACT` activation/query, and `CGATT` attach/query.
The prototype starts with its existing context 1 bearer active; definitions
preserve APN spelling, unsupported families/options fail, active contexts cannot
be redefined, detach clears activation, and reattach alone does not reactivate.
The link now also requires attachment and an active context. Multiple context
IDs share one synthetic RNDIS bearer; this is not independent carrier routing.
Radio/registration suspension retains activation intent for automatic recovery,
so physical modem teardown semantics remain unqualified. Seventy-two focused
modem tests pass, including cross-port context state, atomic rejection and link
callbacks. Native guest PDP commands and packet gating still need live tests;
IPv6, PPP, carrier provisioning, addresses and full command options remain open.

Live managed-guest IPv4 PDP acceptance now passes in
`build/emulator/modem-pdp-r3-20260925/`. Literal AT transcripts prove cross-port
context definition/query and activation state. Interface-bound packet tests
observe up → deactivated/down → activated/up → detached/down → reattached/down
→ activated/up. Activation while detached returns ERROR. GUI changes and an
external attached MCP client were exercised in the same owned runtime before
the PDP probe. The bounded serial channel correctly refused the first oversized
inline probe; the second attempt completed guest checks but kernel console
messages contaminated JSON parsing. The final driver uploads the probe and
downloads its separate result file, preserving logs and exact transcripts.
Guest temporary probe/result files were removed after success. Shutdown was
clean without forced cleanup, ext4 superblock/checksum checks passed, and the
base SHA256 was unchanged. The full suite passed 857 tests in 88.509 seconds
(`full-tests-modem-pdp-20260925.log`); this does not close the broader physical
modem, multiple independent bearer, migration or release gates.

Owned modem lifecycle hardening adds a single absolute acknowledgement deadline
(rather than a fresh timeout per received byte), immediate IPC shutdown when
the worker exits, and retention of the original worker failure. Workspace
inspection now exposes sampled worker health without claiming observed link
state. Fault-injection tests establish that lost QMP acknowledgement wakes the
caller without another link write; an uncertain clean stop retains ownership,
while explicitly forced stop terminates the exact owned VM and releases its
resources while still reporting the modem error. Focused coverage passes 74
modem tests, 15 runtime tests and 36 MCP tests. These injected-failure tests do
not qualify USB reset behavior, migration, or physical recovery.

The lifecycle regression run passed 861 tests in 87.452 seconds
(`full-tests-modem-lifecycle-20260925.log`). USB reset review then found the
inherited QEMU USB-network reset callback was empty. The composite networking
patch now clears RNDIS replies, activation/filter state and packet buffers on
reset, while leaving external SIM/radio state intact. Extracted upstream state
declarations retain their original copyright and MIT permission notices.
A fresh immutable QEMU generation is building; guest reset qualification is
not yet claimed. The managed validator's new `--usb-reset` option uses Linux
USBDEVFS_RESET only inside the disposable guest after matching exactly one
1e0e:9001 device, then repeats registration and PDP checks. Seventy-five focused
modem tests pass; the reset handler still requires the completed build/live run.

The new immutable QEMU build and live USB reset run now pass. Evidence is
retained in `build/emulator/modem-reset-20260925/`: native Linux logs show
option-driver disconnect/rebind for all five serial interfaces and RNDIS
unregister/re-register around a full-speed USB reset. The probe additionally
requires Forge manufacturer/product strings, refusing a physical modem with
the same VID/PID. Guest registration remained roaming, and the complete PDP
packet sequence passed again after reset (up/down/up/down/down/up). Shutdown
was clean with no forced cleanup, checked ext4 superblock clean state, and
unchanged base image hash. This qualifies the tested USB reset path, not
power-cycle behavior, unplug/replug, in-flight transfer reset or VM migration.
The tested QEMU executable SHA256 is
`4a7ef0ceec3e5caec4f00510d48e2ef2073d5c5287fce0888f2b6a83ebf4fd62`,
from immutable generation
`fd637e248a7247d0df81bfebb2ac26711cb920ec5d78f37aed02d650964d3117`.

USB cable-state control is now implemented via the model's settable attachment
property, preserving the external worker/SIM/PDP state. The controller/MCP
`modem_connection` job requires device-control, validates QOM identity before
writing, and records dispatch/readback without retrying uncertain effects.
Seventy-eight modem tests pass, including identity mismatch and lost-response
guards. A new immutable QEMU generation is building; `--usb-hotplug` on the
managed validator will check guest interface disappearance/reappearance and
repeat registration/PDP checks. Live hotplug qualification remains open.
To make build space, the old f1ea7a08 generation's generated source tree was
archived to `build/emulator/qemu-modem-source-f1ea7a08.tar.gz`, compared against
the extracted tree with tar diff (exit 0), then the redundant extracted tree
was removed. Its binary and evidence remain; source can be restored from the
verified archive. No user source or guest image was removed.

The hotplug-capable QEMU build completed, and Workbench cable-button tests pass
(79 modem, nine scoped-client, 42 GUI tests). Live disconnect then exposed a
host-controller crash, retained in `build/emulator/modem-hotplug-20260925/`:
`usb_ep_get: Assertion dev != NULL failed`. The controller job failed with a
reset QMP connection; no successful hotplug or clean guest shutdown is claimed.
The failing QEMU executable SHA256 is
`f79b3b3092d8b58e64f26ec0e7909ae6b773deb65bf22f65cbd63a03db4e9f84`.
Inspection found missing-device dereferences in DWC2 deferred service and async
completion. `dwc2-detached-device.patch` now halts missing-device deferred
channels with transaction-error status and handles vanished async devices
without endpoint lookup. Patch dry-run passes; a fresh build/live hotplug
retest is still required. The guest overlay from this failed trial is retained
as crash evidence, not an export candidate.

The DWC2 missing-device fix builds successfully in isolated generation
`bd7ddee671029c829ba6c381324ca3a763557850186e2ce7976e9315f44eae2b`.
The executable SHA256 is
`50ec4b247c4374e1711a14df2535aefcb20de39f18810d63389a010b97037673`;
build evidence is `build/emulator/qemu-modem-hotplug-fix-build-20260925.log`.
All 79 modem tests, nine source-cache tests and 42 Workbench GUI tests pass.
The fresh `modem-hotplug-r2-20260925` guest acceptance run passes with external
MCP, USB reset and GUI cable controls enabled (process exit 0). Evidence is
retained in `build/emulator/modem-hotplug-r2-20260925/` and its sibling log.
The guest observes all five serial ports and the RNDIS interface disappear on
disconnect and return on reconnect. Registration remains 5, and repeated PDP
AT operations and packet gating pass after reset and hotplug. Shutdown needs
no forced cleanup; the ext4 superblock is clean with its checksum checked,
and the base image hash is unchanged. This fixes the reproduced disconnect
crash in this acceptance sequence; in-flight stress, migration and physical
modem fidelity remain separate, unqualified gates.

To fit this build, redundant extracted source trees for generations `821f299e`
and `fd637e24` were archived and compared with `tar -d` (both exit 0) before
removal. Their binaries and evidence remain. Recovery archives are
`/dev/shm/uconsole-modem-build.4VYSoa/emulator/qemu-modem-source-821f299e.tar.gz`
and `qemu-modem-source-fd637e24.tar.gz` in that same directory; these archives
are volatile RAM-backed storage, not persistent backups of user data.

Repeated offered-traffic hotplug acceptance now passes on that same DWC2 build.
`validate_modem_workbench.py --usb-hotplug --hotplug-cycles 5` starts a bounded
guest ping producer before each disconnect, waits for an initial successful
reply, and records packet counts. Each cycle verifies disappearance and return
of five serial ports and one RNDIS interface, registration 5, and the full
PDP activation/detachment/packet-gating probe after reconnect. All five cycles
pass; the first producer attempted 143 packets, receiving three before loss
across detach. The producer's old interface socket is not expected to resume;
fresh post-reconnect probes establish recovery. Evidence is retained in
`build/emulator/modem-hotplug-stress-20260925/` and its sibling log. The run also
includes external MCP and USB reset, clean shutdown without forced cleanup,
clean/checksummed ext4 superblock and unchanged base hash. This is bounded
offered-traffic testing, not deterministic proof of a USB transaction in flight
at the detach instant, exhaustive stress, power cycling or migration.
The modem suite passes 81 tests; five focused validation tests additionally
check cycle bounds and reject incomplete or inconsistent traffic evidence.
The complete Python regression suite passes 869 tests in 87.500 seconds
(`build/emulator/full-tests-modem-hotplug-20260925.log`). The stricter traffic
packet-count oracle was also checked against all five retained live results.

Artifact retention cleanup (2026-09-25): removed only the redundant initial
export `build/emulator/forge-roundtrip.lBLAbP/acceptance/enhanced.img` after
verifying its SHA256 matched the retained `acceptance/reimported/base.img`:
`06634fbc4778d4698d14a445c7029840b297a034d75a83eeab50c5c3c4c54d8d`.
The completed round-trip record reports that same digest; no visible QEMU
command used this fixture and `fuser` reported no user of the removed file.
It can be recreated by copying that retained base to the original export path.
All overlays, logs, manifests and validation evidence remain. The separate final
export at `acceptance/reimported/gui-attachment-402820a5a1694c878c6686b259fa77d4/files/enhanced.img`
was retained and freshly verified as
`c817ffd8ac00f0e2bb57ae57b67c29a9b8074c81f17c7082481e0497a5bbdc7a`.
Historical records referring to the removed duplicate are not rewritten.
The `821f299e` and `fd637e24` source-recovery archives were then copied from
RAM-backed storage to `build/emulator/qemu-modem-source-821f299e.tar.gz` and
`build/emulator/qemu-modem-source-fd637e24.tar.gz`. Both byte comparisons passed
before deleting only the redundant RAM-backed archive copies. These source
archives now survive host reboot; compiled generation trees were not changed.
After recovery-inspection integration and cleanup, the full regression suite
passes 877 tests in 90.294 seconds, retained in
`build/emulator/full-tests-recovery-inspection-20260925.log`.

Private native recovery preparation (2026-09-25): following the owner's
physical power-cycle confirmation, the healthy alternate-firmware handoff
trial completed and the original boot configuration was restored. This does
not qualify failed-boot fallback or whole-card restoration.

A fresh root-only target directory `/var/tmp/forge-native-recovery.csks3c`
now contains a native recovery build for `6.12.62-v8+`. Its credentials were
captured locally from the approved active Wi-Fi profile after target identity
and profile-stability checks. Fresh, distinct recovery client and server SSH
keys were generated; the client private key remains on the development host.
The image is 37,246,370 bytes with SHA256
`eac8139e571e743976de5c9e0757557dab5fa71751d40fa18b50e7db230738da`.
Private host retention is `build/emulator/recovery-private-20260925/`.
The image embeds network credentials and a recovery server private key: it
must never be included in public artifacts or release uploads. This step
made no boot-partition changes and performed no deployment or reboot.

At this earlier checkpoint native recovery Wi-Fi was unqualified and the
inherited PMF setting was compiled as required protection. The effective-PMF
trial documented above supersedes that policy and connectivity result.
The watchdog keeper still has a five-minute
maximum lifetime; renewable backup/restore leases and safe recovery selection
through interrupted restoration remain required before whole-card work.

The fresh Linux regression run initially caught a stale two-resource MCP
assertion. It now checks the exact five-resource set and rejects cross-workspace
reads for every resource type. All 891 tests pass on rerun; evidence is retained
in `build/emulator/full-tests-private-recovery-r2-20260925.log`, with the initial
failure retained in the corresponding non-`r2` log. The nine client-session
tests also pass independently. This is not native recovery boot qualification.

Private physical image publication (2026-09-25): a fresh fstab preimage and
paired transaction are retained in
`build/emulator/physical-recovery-private-mount-20260925/`. The reviewed plan
`78a233885a8499787daeaffe3fa000853a855b63e0ec0c467dfbcb01cd7152a4`
changes only the boot entry's ownership/masks; target `findmnt --verify`
accepted it. Apply was acknowledged. One normal reboot returned on the same
machine/root/boot partition with boot ID
`679c8467-468e-420e-9506-42aafa885ae1`, effective 0077 masks, root-owned 0700
boot paths, and verified unprivileged read denial. The earlier privacy test
directory was preserved rather than reused.

The schema-2 image publication plan
`27649868fc825d1456ead1719345fd59b61ef82b69d8f6cb2d058b63848137c7`
is retained under `build/emulator/recovery-private-20260925/publication/`.
Preflight verified private persistent policy and absent destination/scratch.
The journaled publisher then acknowledged the exact private image at
`/boot/firmware/forge-recovery-8666c2682dbf43089d46cc1d8e2e30b0.img`.
Post-publication inspection confirms matching bytes, private metadata, absent
scratch and unchanged boot identity. No selector was changed and no recovery
boot was requested. The device remains on its normal system.

The private mount policy intentionally remains applied while this credential
image exists. Remove the exact image through the paired publication restore
operation and verify absence before restoring public boot permissions; never
restore the original fstab first. This access control does not encrypt the SD
card against physical removal. All 57 recovery tests and six privacy tests pass
(`build/emulator/recovery-prepublication-tests-20260925.log` for recovery).
Independent physical failed-boot fallback, recovery networking, renewable
leases and interrupted whole-card restoration remain unqualified.

Private RAM selector preparation (2026-09-25): the staging planner now accepts
qualified root-owned FAT 0700 metadata as well as the older 0755 metadata,
preserving the selected mode in every desired file. The new recovery-specific
preparer requires all existing guarded boot files to be private root/0700 and
binds the selector host to the image-publication host. Public or otherwise
unqualified metadata is rejected before creating plans. Its plans still grant
no deployment authority and retain publication, fallback and physical-recovery
gates.

Fresh preimages and unapplied paired RAM-trial plans are retained in
`build/emulator/physical-private-ram-trial-20260925/`, with nonce
`7813a15624ab4623a39be4a40743afcd`. A fresh publication inspection verified the
private policy, matching image and absent scratch before preparation. The
selector references the exact previously published image and matched firmware
pair. No phase has been dispatched and no recovery boot has been requested.
The full regression checkpoint passes 893 tests in 88.109 seconds
(`build/emulator/full-tests-private-staging-20260925.log`).

A separate non-deploying failed-root trial compiler now strips native root,
resume and initramfs selections, selects `/dev/ram0` with missing init paths,
and sets `panic=0`. It preserves the alternate firmware watchdog configuration
and never changes normal config/cmdline preimages. Return to normal SSH alone
is explicitly not proof that this trial executed: observed console failure is
an additional gate. This trial has not been prepared or run on hardware.
The final focused suites pass 11 firmware-trial and nine tryboot tests; the
last three added tests postdate the 893-test full-suite checkpoint.
The watchdog behavior under test follows the upstream
[kernel watchdog configuration](https://www.raspberrypi.com/documentation/computers/config_txt.html),
not an assumption that an active runtime watchdog proves failed-boot recovery.

The failed-root recipe now has a diskless execution validator
(`tools/validate_failed_root_trial.py`). It integrity-checks the compiled command
line, rejects persistent-root/resume arguments and conflicting panic/init
settings, adapts only console routing, and attaches no disk, initrd or network.
The console must identify the exact trial nonce and the RAM-device VFS panic;
init execution, a mounted root or a software reboot rejects acceptance. The
owned guest must remain alive at that panic for five seconds before cleanup.
This tests failure injection, not firmware or watchdog fallback.

The live run in `build/emulator/failed-root-diskless-r2-20260925/` passes,
observing the panic for 5.009 seconds before terminating only its owned QEMU.
Kernel SHA256 `4871c5bbb93f24aadfc574eb6af392464f11bbc4f0eb50e1b40eff3e78d67276`
was freshly verified equal to the physical target's `/boot/firmware/kernel8.img`.
The first run is retained in `build/emulator/failed-root-diskless-20260925/`:
its checker rejected the newer kernel's explicit `"/dev/ram0" or` wording,
despite the intended panic. The checker now accepts both precise RAM-panic
formats and still rejects a named persistent device or another device number.
Three focused validator tests pass. Physical boot ID remains
`679c8467-468e-420e-9506-42aafa885ae1`; no physical trial was dispatched.
The full suite passes 899 tests in 87.913 seconds, retained in
`build/emulator/full-tests-failed-root-20260925.log`. The console-format fix
also passed the focused validator suite and live second run separately.

Agent/package integration checkpoint (2026-09-25): the shipped forge skill now
routes composite-modem work to a focused reference, describes all five resource
kinds, and documents recovery inspection without granting publication/reboot
authority. The skill validator passes. Native archive qualification now checks
that skill references resolve inside the shipped skill directory, records their
hashes, and queries the actual installed MCP launcher for modem/recovery tools
and the complete scoped resource set. Seven archive-validator tests pass.

A fresh Linux ARM64 archive built under `build/agent-package.sR3ZF9/` passes
launcher help, MCP initialization/tool/resource discovery, and isolated Tk
edit/save without changing bundled source. Evidence is in its
`acceptance/native-archive-acceptance.json`; archive SHA256 is
`029709c1064f131ef898234e001cf78c8aae2dfe933c7d5efa7ea1bc199386f1`.
Archive inspection finds no guest images, recovery credential files or private
emulator-build evidence. This does not qualify native x86_64/macOS packages,
installed guest boot, an independent coding-agent workflow, or a release.

Fresh native macOS ARM64 package qualification also passes on Puck. The isolated
snapshot is `/tmp/uconsole-agent-package.PiAg0R`; its initial packaging attempt
failed because the transfer omitted three flashing-helper assets. Those source
assets were added, and the second build passed. Both build logs remain under
`build/emulator/macos-agent-package{,-r2}-20260925.log` on the development host.
This was a snapshot error, not a bypassed packaging failure.

The installed archive passes both launcher help commands, MCP initialization,
ADC/modem/recovery tool discovery, all five scoped resources, shipped skill
reference resolution and isolated GUI edit/save without changing bundled source.
The GUI used native Python 3.12.14 and Tk 9.0.4, with process-local saved-window
restoration suppression. The archive and acceptance JSON were copied back and
checksum-verified under `build/emulator/macos-agent-package-20260925/build/`;
archive SHA256 is
`83bf5e29e3410fb439f53bd59ed74bb4c6c911e801ba698325173904237665c7`.
Focused native archive, firmware-trial, tryboot and failed-root validator tests
pass in `build/emulator/macos-agent-focused-tests-20260925.log`.

The earlier full-suite process PID 41406 was confirmed live and was not stopped,
restarted or counted as passing. This package qualification does not establish
that suite's completion, installed macOS guest boot, x86_64 qualification or
physical fallback. The physical target's boot selection was not changed here.

Recovery renewal groundwork (2026-09-25): the C watchdog keeper core now has an
optional renewal callback with a bounded contract. Each request can extend the
deadline by at most 300 seconds from its observation; shorter requests cannot
shorten an existing lease. Expiry is checked before renewal, so late requests
cannot revive expired sessions. Total lifetime is capped at 24 hours even with
continuous renewals. Invalid, non-finite or failed requests and a backwards or
non-finite clock stop keepalives. Fake-clock C execution covers renewal, lost
renewals, the hard cap, request failure and invalid time without touching any
host watchdog device; production C also compiles with warnings as errors.

This hook is intentionally **not enabled in production**: the native device
keeper still supplies a null callback and retains its five-minute maximum.
The separate runtime software deadline is unchanged. Before enabling renewal,
the authenticated/fenced request transport and persistent recovery boot
selection through interrupted destructive restoration must be implemented and
qualified together. No new RAM image was built or published and the physical
target was not modified. All 57 focused recovery tests pass, recorded in
`build/emulator/recovery-renewal-core-tests-20260925.log`. This is a tested
renewal algorithm, not a completed long-running backup/restore feature.

Persistent recovery hold groundwork (2026-09-25):
`forge_recovery_hold.compile_hold` derives a review-only persistent `config.txt`
replacement from the exact compiled RAM selector. It first verifies all nine
staged boot preimages against the original backup and matched recipe, requires
private metadata and matching target identity, and retains every dependency.
Only normal `config.txt` changes; its selected recovery command, image and
alternate firmware are the same as the trial's. No target writes occur.

The returned artifact is deliberately rejected by the generic file executor:
it is not an approved apply/restore plan. Deployment and normal-boot release
remain unauthorized. A consumer must qualify the physical RAM boot, persist
the hold, verify an actual reboot back into recovery, and exclude the entire
boot partition and partition table from destructive writes. A whole-card raw
write would destroy the recovery mechanism and is explicitly not authorized.
Normal-boot release requires separate proof of complete root restoration and
native boot dependencies. No automatic inverse is exposed before those gates.

Four focused tests cover the exact single-selector change, immutable inputs,
all missing/changed dependencies, private metadata, target identity and refusal
by generic dispatch. All 61 recovery tests pass, retained in
`build/emulator/recovery-hold-tests-20260925.log`. This does not yet provide the
hold dispatcher, qualified persistent recovery boot or block restoration. No
physical configuration or published recovery image was changed in this step.

Recovery Python runtime (2026-09-25): the RAM-image builder now includes the
target's distribution `/usr/bin/python3`, standard-library Python sources and
native extension dependencies. It excludes site/user customizations, tests and
third-party package directories, rejects selected symlinks escaping stdlib and
bounds the source count/size. It requires native `/usr/lib/python3.x` layout;
virtualenv/local interpreter packaging is not silently substituted. Four builder
tests and all 63 focused recovery tests pass.

A fresh native disposable-credential image built and passed extracted-image
SSHD configuration plus isolated Python import/ctypes/hash preflight on the
uConsole. Target evidence is `/var/tmp/forge-python-recovery.bs1Pj7/proof`;
the retained host image, manifest, log and acceptance are under
`build/emulator/recovery-python-native-20260925/`. Image size is 46,115,550 bytes,
SHA256 `5afeee636f8d7208cb44c174f10e54f0313651ccc436e15bf04bd440589e7041`.
It contains disposable test credentials only and was not published to boot.

The same image passes diskless QEMU RAM boot, authenticated SSH and Python
runtime preflight (`validate_recovery_watchdog.py --require-python`). Killing
only the guest's identity-checked watchdog keeper then produces modeled expiry
after 15.519 seconds, with QEMU exit 0 and no forced cleanup. Evidence is in
`build/emulator/recovery-python-diskless-20260925/` and its sibling log. This
proves the needed interpreter works inside the running RAM environment, not
physical Wi-Fi, persistent target identity/ledger storage or hold dispatch.
Renewal remains disabled; the existing physical credential-bearing image and
boot selector are unchanged. No persistent filesystem was attached to QEMU.

RAM-session identity gate (2026-09-25): `forge_ram_identity` supplies a fixed,
read-only Python probe and verifier. Physical verification requires the expected
hardware serial, kernel release, exact trial/recovery/root/init markers, root
privilege and a boot UUID; emulator markers are rejected. Only RAM/pseudo
filesystems with device major zero are accepted, with exactly one RAM root.
Block mounts, overlays, duplicate roots and malformed observations fail closed.
Emulated observations require an explicit mode and emulator marker and are
never reported as physical qualification. Success grants neither mutation nor
fallback authority. Four focused tests pass.

The actual normally booted uConsole was read through trusted SSH and correctly
rejected for missing the recovery marker. Evidence is private under
`build/emulator/physical-ram-identity-rejection-20260925/`; no target files were
written. The positive check passes through pinned-key authenticated SSH in the
diskless Python-capable recovery guest, using a fresh trial nonce and RAM-only
mount inventory. Evidence is `build/emulator/recovery-identity-diskless-20260925/`.
The subsequent guest-only keeper-loss test still passes: modeled expiry after
15.480 seconds, exit 0, no forced cleanup. This gate is not yet connected to a
physical hold dispatcher, and successful physical RAM boot remains unqualified.

Dedicated recovery SSH transport (2026-09-25): `forge_ram_transport.RecoveryProbe`
exposes only the fixed read-only RAM identity probe, not caller-supplied commands.
It pins private, owned, singly linked key/trust files at construction and checks
them again before each request. Normal SSH config, authentication agents, global
known-host files, connection multiplexing and forwarding are disabled. Host keys
remain strict; no TOFU or fallback to normal device credentials is attempted.
An optional expected boot UUID rejects a reconnected but different recovery
boot. Failures are not automatically retried and confer no mutation authority.
These application checks are not a sandbox against same-user credential races.

Eight RAM identity/transport unit tests pass. The actual dedicated transport
also passes initial inspection followed by a boot-bound recheck through pinned
SSH in the diskless recovery guest. Evidence is retained in
`build/emulator/recovery-transport-diskless-20260925/`. The subsequent guest-only
watchdog keeper-loss test still passes after 15.456 seconds, QEMU exit 0, with
no forced cleanup. This remains emulator transport qualification, not physical
networking, failed-boot fallback or authorization to apply the recovery hold.
The full regression suite passes 915 tests in 88.346 seconds; evidence is
`build/emulator/full-tests-ram-transport-20260925.log`.

Read-only restoration layout binding (2026-09-25):
`forge_recovery_layout` reads only `/dev/mmcblk0`'s DOS header and kernel/sysfs
geometry. It checks block-device identity, CID, ioctl size/sector size, and
stable repeated metadata/header reads. The planner requires the approved CID
and disk ID, checks the actual two-primary-partition FAT32/Linux layout against
kernel partition extents, and rejects overlaps, out-of-bounds extents, extended
partitions, GPT and extra partitions instead of guessing. Another layout needs
a separate planner. The resulting root extent protects all preceding bytes
(including partition table and boot partition) and any trailing region.
It explicitly grants no restore/whole-card write authority and makes no claim
of consistent backup from a live system.

The physical card's read-only result matches an independent `sfdisk --json`
capture: root extent 31,373,918,208 bytes after a protected 541,065,216-byte
prefix. Private observations/header, extent and independent capture are retained
under `build/emulator/physical-recovery-layout-20260925/`. No card data was
written. `RecoveryProbe.inspect_storage` brackets the fixed metadata probe with
RAM-session checks of the same previously bound boot UUID; a reboot or a
non-RAM session prevents accepting the result. Four layout tests and nine
identity/transport tests pass. The integrated storage probe still needs live
RAM-guest disk-fixture qualification; this is not a backup or restore writer.

Live storage-probe qualification (2026-09-25): the watchdog validator's new
`--storage --require-python` mode creates an exclusive synthetic 64-MiB SD image
with known DOS partitions and a root-extent marker. It accepts no existing disk
as the fixture. The first attempt is retained in
`build/emulator/recovery-storage-guest-20260925/`: QEMU rejects a read-only SD
backend before boot. The second attempt is retained in the corresponding `r2`
directory: Linux exposes this emulator's SD card as `mmcblk1`, not the physical
target's `mmcblk0`. No fixture was mounted or written by the probe.

The layout reader/transport now take an explicit whole MMC device, bounded to
`/dev/mmcblk` plus one or two digits. Partition paths, arbitrary devices and
shell syntax are rejected; CID, disk ID and geometry remain independently
bound. Physical callers still default to their qualified `mmcblk0`; the emulator
validator explicitly selects its `mmcblk1`, without aliasing device nodes.

The `r3` live run passes authenticated, boot-bound RAM storage inspection against
the expected header hash, total size, root offset (8 MiB) and root length
(56 MiB). The whole fixture retains SHA256
`948cc30d738f0f1e7acabf0ce59bb01277796aa3c5cc59130394864d451752d9`.
It is a writable QEMU backend with a read-only probe, not hardware write
protection; the acceptance record states that distinction. Guest-only watchdog
expiry also passes after 15.448 seconds, exit 0, without forced cleanup.
Evidence is `build/emulator/recovery-storage-guest-r3-20260925/`. Seventy recovery
tests and nine RAM identity/transport tests pass; the recovery log is
`build/emulator/recovery-storage-tests-r2-20260925.log`. No physical writes or
reboots occurred. Consistent backup and restoration remain unimplemented.

Root-partition backup implementation (2026-09-25): `forge_recovery_backup`
now streams a bound recovery root partition through pinned SSH into an exclusive
private gzip artifact. The remote worker rechecks the RAM boot, card/layout and
exact partition size, opens only the root partition read-only/exclusive, and
rechecks session/layout before acknowledging completion. The host retains a
durable plan before transfer, bounds diagnostics/output/time, reserves 128 MiB
of host free space, and retains partial files with an incomplete acceptance
record on failure. It independently decompresses the result and verifies exact
uncompressed length and the source SHA256 before accepting the byte backup.
No restore/write API is exposed. Backup files contain user data and are private,
not release artifacts.

The actual emulator `--storage --backup-root` test passes against the 56-MiB
synthetic root extent: 58,720,256 bytes, SHA256
`fc6b00d0852042694de3cd76c2794a54660c73e3fd6828cddf630844fc181dd9`,
compressed to 256,298 bytes. This also matches an independent read of the host
fixture's root bytes; the whole synthetic card hash remains unchanged.
Evidence is in `build/emulator/recovery-root-backup-20260925/`, including the
private `root-backup/` plan, archive and acceptance. Guest-only watchdog expiry
still passes without forced cleanup. Four new unit tests cover transfer,
independent verification, overwrite refusal, transfer failure, size/deadline
limits, host-space exhaustion and incomplete-manifest retention. Seventy-four
recovery tests and nine RAM identity/transport tests pass.

This is root-partition byte backup, not a complete system backup or filesystem
consistency/restore qualification. The transfer is capped at 240 seconds while
renewal remains disabled. Whole-system metadata/boot backup bundling, longer
physical transfers, competing-writer exclusion at the controller, persistent
recovery hold, restore execution and interruption qualification remain open.
The physical uConsole was not modified or rebooted during this test.

Live interrupted-backup qualification (2026-09-25): callers can now supply a
positive compressed-byte budget, frozen into the backup plan and capped by the
intrinsic output bound. The synthetic-card validator's `--interrupt-backup`
mode deliberately limits the first transfer to 4096 bytes. The real SSH stream
exceeds that budget, is stopped, and retains an incomplete acceptance record
and partial archive. The validator then observes release of the guest worker's
exclusive read claim, verifies the same RAM boot, and starts a separate backup
in a fresh directory. It never kills an unidentified guest process or resumes
the partial gzip file.

This sequence passes in
`build/emulator/recovery-backup-interruption-20260925/`. The fresh backup matches
the independent 56-MiB host root fixture. Hashes of the first plan, incomplete
acceptance and partial archive remain unchanged, as does the entire synthetic
SD image. Guest-only watchdog expiry still passes after 15.443 seconds, exit 0,
without forced cleanup. Seventy-five recovery tests and nine RAM transport/
identity tests pass, with the recovery log in
`build/emulator/recovery-backup-interruption-tests-20260925.log`. This qualifies
an explicitly size-capped transfer failure and a fresh backup, not interruption
at every byte, physical backup, controller cancellation or interrupted restore.

Renewable offline-backup recovery (2026-09-25): the opt-in recovery runtime now
uses a nonce/boot/owner-bound renewable lease, with independent software expiry
and hardware-watchdog consumers. Renewal is for read-only backup only; it grants
no root-write or normal-boot-release authority. Duplicate acknowledgements do
not extend the lease. The default non-renewable recovery path remains available.
The physical soak in `build/emulator/physical-renewable-lease-trial-20260925/`
passed beyond its initial deadline, returned to the normal system after renewal
stopped, and restored all nine guarded boot-file preimages. No root-partition
writes occurred. This does not qualify physical keeper-loss or interrupted
restore recovery.

The backup worker now supports an explicit whole-card byte scope, preserving
the partition table, gaps and boot partition as well as the root partition.
Leased transfers have bounded duration and host-space preflight; renewal loss
terminates the transfer and retains incomplete evidence. The synthetic-card
test in `build/emulator/recovery-whole-card-guest-20260925/` verified all
67,108,864 bytes against the independent fixture SHA256
`948cc30d738f0f1e7acabf0ce59bb01277796aa3c5cc59130394864d451752d9`,
with unchanged fixture storage and modeled watchdog fallback. Physical
whole-card capture is underway in `physical-full-card-backup-20260925/`; no
physical completion or restore qualification is claimed here.

Host-only archive materialization (`forge_recovery_archive`) requires a completed
backup receipt and creates an exclusive private image. It rejects links,
non-private inputs, insufficient space, expanding/corrupt archives, changed
source metadata and mismatched byte lengths/hashes. Failures retain incomplete
output and evidence. It never mounts a filesystem or accesses a target device.
Nine focused tests and all 161 recovery tests pass. Materializing the completed
synthetic whole-card backup also reproduces the independent fixture hash;
evidence is in `build/emulator/recovery-whole-card-materialized-20260925/`.
Byte verification alone is not filesystem consistency, bootability or restore
authorization. In particular, a physical capture made during recovery includes
the temporary recovery boot staging; restoration must account for the retained
preimages rather than treating that archive as an unmodified normal-boot image.

The refreshed Linux suite passes all 1,016 tests (91.468 seconds), including
the whole-card backup and archive-materialization changes. Log:
`build/emulator/full-tests-card-archive-20260925.log`. This is host regression
evidence, not completion of the pending physical backup or release gates.
