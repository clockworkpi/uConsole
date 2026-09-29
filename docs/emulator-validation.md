# Local CM4 emulator validation

This cumulative record primarily covers Linux ARM64 (`sparky`) validation using QEMU
10.2.4 and the evolving repository patch stack. Individual sections identify
their coverage and retained evidence; older results do not qualify later changes.
These results establish a usable development environment,
**not complete uConsole hardware equivalence**. See [coverage](emulator.md).

## Inputs

### Bounded host-test diagnostics (2026-09-26)

The superseded macOS test job in manual run `36245917832` was cancelled after
the current-revision host/package workflow `36248033054` passed. Its retained
log ends after a keyboard-oracle test and does not identify the stall's cause;
the reviewed x86 desktop/package job had already completed successfully.
`make check` now retains all-thread stack dumps every five minutes while keeping
unittest discovery and exit semantics. CI's complete Linux/macOS test steps have
a 20-minute limit. These are diagnostic bounds, not a claimed fix for the
unexplained stall or permission to treat cancelled tests as passing.
The complete Linux Xvfb suite passes 1,570 tests (one skip) plus ShellCheck in
`build/emulator/full-tests-traceback-runner-20260926.log`. Native macOS passes
all 18 focused runner/keyboard-oracle tests in
`build/emulator/macos-traceback-runner-tests-20260926.log`, using fixture
`/private/tmp/uconsole-test-traces.kzpGbi2a`. The runner tests exercise actual
child-process success/failure exit codes and periodic stack output during a
slow test without changing its successful result.

### Linux x86_64 desktop after UART/QMP correction (2026-09-26)

The native x86 package/desktop job in run `36245917832`, revision
`aa2d2204dd6f721766e789a7f257b05250d7f2d3`, completed successfully. Its public
artifact is retained under `build/emulator/x86-public-onboarding-36245917832/`.
Visual review confirms Terminal displaying the typed command and its output
(`screen-94c975fda439440a8b7e7c14e2bce666.png`), the normal desktop after reboot
(`screen-97cde0e3b0f543639fae462dd62e2f61.png`), and Terminal reopened by mouse
after reboot (`screen-1c19d18fe7994fe0b473f70ea4405189.png`). Serial markers
record boot UUIDs `7f900c6c-330a-480c-b0a3-3fa3ddcdae3d` and
`5972b7ca-7f1e-4a4d-b152-165225c22a78`; the post-reboot output now completes.
The final status is stopped with QEMU exit 0, and both recorder and driver
report no forced cleanup. The real diskless backpressure regression also
passes: 65,536 bytes drained, no replayed input, clean QEMU exit.

This is a reviewed desktop/input/reboot/shutdown pass with the existing stock
kernel caveat: shutdown emits an atomic-I2C warning through `axp20x_power_off`.
It is not a warning-free guest, full filesystem check, or physical display/input
equivalence claim. The separate macOS host-test job in that manual run was
still running at review time; no whole-workflow success is claimed here.
Later host/package qualification at `1873d38` passed in run `36246701895`, with
all three archive hashes and backup-policy module hashes independently checked.
Extracted Linux ARM64/macOS resources each passed 23 focused backup/panel tests
without skips; logs are under `build/emulator/qualification-1873d38-20260926/`.

### Owner-side backup policy drafting (2026-09-26)

The recovery panel can now turn its completed enrollment into a backup-only
policy draft using pinned, read-only SD inventory. It holds only the local
session lock, checks card geometry twice on the pinned boot, and opens the
result in the existing review panel without approval, lease renewal, archive
creation, reboot or write grants. Busy/uncertain sessions, changed credentials,
card or boot, and overlapping output paths (including a parent symlink alias)
fail closed. Declining the GUI confirmation makes no target contact.

All 23 focused backend/panel tests pass on native macOS in
`build/emulator/macos-backup-policy-tests-20260926.log`, using fixture
`/private/tmp/uconsole-backup-draft.4CwPuqu9`. The complete Linux Xvfb suite
passes 1,566 tests (one skip) plus ShellCheck in
`build/emulator/full-tests-backup-policy-20260926.log`. These tests use controlled
SSH observations; the active physical deployment was left untouched. This is
not physical qualification of the new GUI or completion of the guided workflow.

### Recorder UART/QMP backpressure correction (2026-09-26)

The second x86 desktop trial, `36243425771` at `c54e5d2`, again failed after
reboot. Its retained diagnostic now identifies `query-name` timing out during
`qmp_capabilities` reply, before the mouse input was sent. A separate read-only
status probe failed at the same phase, while serial output stopped mid-line.
Evidence is in `build/emulator/x86-public-onboarding-36243425771/`.

The recorder performed synchronous QMP calls on the Tk thread that also drains
its serial socket. QEMU's PL011 writes each UART byte synchronously; a full
socket can hold QEMU's main lock and block QMP. The diskless
`validate_desktop_serial_backpressure.py` reproduces this exact capability-reply
timeout against real QEMU, then proves that draining UART restores QMP.
Linux stalls after 278 undrained bytes; native macOS after 8,192. These are
observations on the tested hosts, not portable socket-capacity guarantees.

Recorder QMP calls and failure diagnostics now use one worker, while completion
callbacks and input sequencing remain on Tk. Competing input is rejected while
an exchange/sequence is pending; failed input is never replayed. With this path,
both hosts drain all 65,536 fixture bytes and complete QMP, then exit their owned
diskless QEMU cleanly without forced cleanup. Logs are
`build/emulator/serial-backpressure-r2-20260926.log` and
`build/emulator/macos-recorder-async-20260926.log`; the latter also records all
16 focused recorder/driver/Tk tests passing. The macOS fixture is
`/private/tmp/uconsole-recorder-io.f7pNsUHt`. CI's optional full desktop trial now
runs this reproduction/regression first and retains its receipt. This fixes a
reproduced harness deadlock, not yet the full x86 desktop acceptance gate.
The complete Linux suite passes 1,556 tests (one skip) plus ShellCheck in
`build/emulator/full-tests-recorder-async-20260926.log`.

### Owner-side fresh recovery enrollment (2026-09-26)

Workbench now exposes **Enroll recovery session…** as an owner-only controller
job. It derives the owner from sealed staging, pins credentials and the expected
new boot, checks RAM identity and firmware selection, and creates an unrenewed
local session. No lease is acquired, target written, reboot dispatched or client
grant increased. A durable per-staging/per-boot claim rejects duplicate attempts;
failed observations preserve evidence instead of resetting a lease sequence.

All 21 focused enrollment/panel tests pass under Linux Xvfb and native macOS,
including changed credentials/staging, wrong owner, reboot during observation,
duplicate claims, declined GUI confirmation and unchanged client permissions.
Logs: `build/emulator/session-enrollment-tests-20260926.log` and
`build/emulator/macos-session-enrollment-tests-r2-20260926.log`. The native fixture
is `/private/tmp/uconsole-enrollment.Qg7v5Zd9`. The full Linux suite also passes
1,548 tests plus ShellCheck in `full-tests-session-enrollment-20260926.log` under
`build/emulator/`; this was before splitting the wrong-owner and changed-boot
subcases into separate tests, both included in the final 21-test focused runs.
These use controlled SSH observations, not the active physical recovery target.
The new UI is not yet physically qualified and does not complete bootstrap
provisioning or the end-to-end guided workflow.

The follow-up removes manual boot-UUID entry: after owner confirmation,
Workbench reads the UUID from the pinned target's verified RAM identity and
uses that exact UUID for all subsequent checks. It refuses to follow a second
boot or replace an existing claim. Explicit-UUID backend callers remain
supported. All 24 focused tests pass on Linux and native macOS in
`build/emulator/session-discovery-tests-20260926.log` and
`build/emulator/macos-session-discovery-tests-20260926.log`. No discovery request
renews a lease or grants target authority.
The full Linux suite then passed 1,552 tests (one skip) plus ShellCheck, retained
in `build/emulator/full-tests-session-discovery-20260926.log`.

### Live coding-agent image round trip through packaged MCP (2026-09-26)

The coding agent in this session exercised the actual macOS archive from
package run `36241101151`, revision `91f04b0`, SHA-256
`69c825de4ed5af99bc3e2a0017c5148426aabfb63b49575b3bd6482f1a6b78bc`.
It followed the forge skill, discovered the GUI owner's resources and grants,
imported the public factory image, checkpointed it and booted maintenance mode.
The agent wrote and uploaded a hardware-neutral `forge-system-report` utility
and four application tests, then executed them through MCP. A read-only probe
found no device-tree model path in this maintenance session; the application
reports an unavailable model without changing native boot settings. That initial
nonzero guest command remains in the transcript, separate from successful tests.

All four application tests passed, and the stopped guest exported as SHA-256
`1156f8b64c6b4b956c72dc13f3d8317b303439d5c08aef0c83a0719d1408ebe1`.
A separate packaged MCP owner fully imported that export into a new workspace,
verified the application/test hashes and reran all four tests successfully.
Both guests stopped cleanly, both MCP clients exited zero, and independent
checks verified unchanged bases/export, unchanged protected boot bytes and
clean/checksummed primary root superblocks. These are not full fsck results.
The app SHA-256 is `454da9feaca20de5a9ab50fec5711b4ef1c0438d9847174d77790069a2a794e3`.

Retained evidence: `build/emulator/live-agent-mcp-20260926/`, including both
stdio transcripts, application source/tests, `verified.json` and `review.json`.
The remote fixture is `/private/tmp/uconsole-live-agent.BjMZzJ94`. Task decisions
came from the live agent; the transport helpers did not implement a task script
or invoke another coding agent. GUI history exposed the same completed job IDs.
Window-only host capture was unavailable, so this is not a visual GUI usability
claim. The idle GUI was terminated only after guest/job completion and client
exit. This public fixture is separate from the private physical deployment and
rollback image; no physical target was contacted and no private images published.

### Release orchestration gates (2026-09-26)

Publication now depends on both extracted native package qualification and the
complete reusable host/GUI test workflow, rather than a separate unjoined CI run.
The local tagging command also requires GUI tests, a current QEMU build and the
full `check-emulator` device gate before packaging or tagging. It uses the same
Tk-capable Python selector as Workbench; Linux requires Xvfb, while macOS uses
native Tk. Six orchestration tests pass on Linux and native macOS, including
failure of each gate before tag creation, missing Xvfb and interpreter probing.
The mocked tests do not themselves qualify guest devices or publish anything.
The actual Linux full suite passed 1,534 tests (one skip), followed by ShellCheck,
using `/usr/bin/python3` under Xvfb. A fresh QEMU build and the complete
`make check-emulator` gate also exited zero. Logs are retained as
`build/emulator/full-tests-release-gates-tk-20260926.log`,
`build/emulator/release-gate-emulator-build-20260926.log` and
`build/emulator/release-gate-check-emulator-20260926.log`.
An earlier run selected a Homebrew Python without Tk and failed; that log is
retained, and prompted reuse of the launcher's interpreter selector.

### x86 public desktop onboarding: partial, reboot gate failed (2026-09-26)

Manual package run `36240936574` at `adc303d` completed account setup and
reached the 1280x720 desktop. A reviewed frame at 1,084.917 seconds shows
Terminal and the output of `echo desktop input verified`, establishing real
mouse launch and keyboard input. After the requested reboot, the frame at
1,319.924 seconds shows only a black screen and pointer. Serial login and `id`
returned, but the requested boot UUID did not appear in the retained tail.
The next pointer action timed out at 1,354.95 seconds; the driver failed and
the recorder force-cleaned its owned disposable VM. QEMU's log has no diagnostic
explaining the stall. This is not a successful reboot or shutdown qualification,
and does not yet establish whether the failure is a guest or QMP/host problem.
Evidence is retained in `build/emulator/x86-public-onboarding-36240936574/`.
The follow-up recorder captures a read-only owned-process thread snapshot and
a separate `query-status` observation before cleanup. QMP timeout messages now
identify connection, greeting, capability negotiation or operation send/reply;
no failed input is replayed and neither command arguments nor process
environments are recorded. This instrumentation is diagnostic, not a fix or a
relaxation of the failed acceptance gate.

### Native macOS guest desktop lifecycle (2026-09-26)

The actual Workbench recorder on Puck, using the Cocoa fix at `015aa4d`, now
completes the public factory guest's account setup and 1280x720 desktop loop.
Mouse input launches Terminal; keyboard input runs
`echo cocoa desktop input verified`, whose output is visually confirmed.
Keyboard-requested `sudo reboot` returns to the desktop with the same `forgeproof`
account and a new boot ID (`e345b814-b2b8-4146-a2bd-ffbdff94a271` to
`bd5300a5-568b-42bc-bdf1-4694438be3c2`). Terminal can again be mouse-launched.
Keyboard-requested `sudo poweroff` logs powerdown, and Workbench reports QEMU
exit zero. The recorder finishes without forced cleanup. Independent stopped
checks verify a clean/checksummed primary root superblock and unchanged public
raw backing SHA-256 `a7b0a2bfa86a45150af1ae70a94ed432718bfec90ffdef54952564bd34f0d788`.
This is not a full filesystem check.

Evidence is retained privately in `build/emulator/macos-cocoa-desktop-20260926/`:
`acceptance.json`, `stopped-verification.json`, recorder/serial logs and reviewed
frames. The native workspace remains `/private/tmp/uconsole-cocoa-desktop.BzSGs2OP/workspace`.
The unmodified factory kernel warns that `i2c-22` lacks an atomic transfer handler
in the AXP poweroff path. Successful shutdown therefore does not establish a
warning-free kernel or the separately tested atomic-I2C enhancement. Initial
form focus needed an interactive correction; one malformed recorder command
was rejected and subsequently corrected. Neither is hidden by the acceptance.
The framebuffer and generic keyboard/mouse remain surrogates, not DSI/GPU or
STM32 composite fidelity. This public fixture is separate from the private
physical enhanced-image deployment and rollback gates.

### macOS native display backend correction (2026-09-26)

Puck's Homebrew QEMU reports Cocoa but no GTK/SDL; the separately built patched
QEMU reports Cocoa and SDL but no GTK. The recorder previously hard-coded GTK,
and Workbench/CLI validation omitted Cocoa entirely. Display choices now share
one definition including Cocoa, while the recorder chooses Cocoa on macOS and
GTK elsewhere. The existing headless default remains unchanged.

The 123 focused emulator, controller, Workbench GUI and recorder tests pass on
Linux and native macOS. A separate native probe starts the patched QEMU with
`-display cocoa`, checks `query-display-options` returns Cocoa and
`query-status` reports the deliberately paused machine, then exits via QMP.
Evidence: `build/emulator/macos-cocoa-probe-20260926.log`. No guest booted in that
probe; it does **not** establish guest desktop interaction or onboarding.
The complete suites subsequently pass 1,527 tests on Linux (one skip) and native
macOS (80 skips), plus ShellCheck; retained logs are
`full-tests-cocoa-display-20260926.log` and `macos-full-cocoa-display-20260926.log`.

### Native x86_64 desktop observation procedure (2026-09-26)

The package workflow has an opt-in manual `capture_public_desktop` input. It
downloads only the public CM4 v3.1 factory image, checks the repository-pinned
SHA256 before decompression, and uses the extracted package's QEMU, desktop
adapters and real Workbench recorder under Xvfb on a native x86_64 runner.
The manufacturer mirror is HTTP; the independent checksum is mandatory.

`tools/drive_forge_desktop.py` drives the recorder using a bounded, ordered JSON
scenario. The initial `factory-desktop-capture.json` samples startup at three,
six and ten minutes; these times are observations, not readiness assertions.
The resulting `scenario-recorded` status and a green capture job are **not** a
desktop qualification pass. Screenshots, guest state, input handling, onboarding
and clean shutdown still need verification. EOF explicitly force-stops this
disposable capture guest and records that fact; it is not clean-shutdown proof.

The artifact allowlist retains screenshots and diagnostic logs/receipts, never
the factory-derived disks or the recorder's password fixture. Do not substitute
a private hardware image into this hosted workflow. PR and tag jobs do not run
the opt-in capture; manual jobs cannot publish a release.

Run `36238221019` at `e2c1b64` completed this capture on native Linux x86_64.
Visual inspection of the 600-second frame shows the 1280x720 Raspberry Pi
desktop welcome screen. Retained evidence is
`build/emulator/x86-public-desktop-36238221019/`, including `review.json`.
The public compressed image SHA-256 is
`ef95242cdb0125e8ed08157400a26d4665acddd083481ec2314acd6e073b74ab`;
the raw backing image is
`a7b0a2bfa86a45150af1ae70a94ed432718bfec90ffdef54952564bd34f0d788`.
No input was sent, and the recorder force-stopped the disposable guest at EOF.
This closes graphical-startup observation only, not desktop qualification.

The optional `desktop_scenario=onboarding` recipe now records account creation,
mouse-launched Terminal, typed output, boot IDs around a requested reboot and
keyboard-requested poweroff. It is derived from the manually observed native
Mac interaction, not yet an x86 pass. Its scheduled inputs assume the pinned
factory UI at 1280x720; they are not readiness detection. Inspect screenshots,
serial results and terminal receipts before accepting any interaction. `finish`
requires Workbench to have released its guest and rejects forced cleanup.
Only the disposable public-image workflow uses this recipe; it never contacts
the physical uConsole or installs a replacement desktop in the image.

### Recovery and agent integration checkpoint (2026-09-26)

The qualification branch at `7da7984` passes all host and package CI jobs.
Extracted package checks cover Linux x86_64/ARM64 and macOS ARM64 GUI startup,
editable firmware source, firmware provenance, and QEMU builds/device tests.
Those package checks do **not** establish the full x86_64 guest desktop loop.
Local Linux and native macOS suites each run 1,519 tests successfully, with one
and 80 platform-specific skips respectively, plus ShellCheck. The separate
Linux root-owned temporary-filesystem staging roundtrip also passes.

`physical-restore-stream-20260925/acceptance.json` now records a completed
physical unchanged-source stream, independent root verification, persistent
RAM hold/release, normal native return, and restoration of all nine boot-file
preimages. All 7,481 root chunks were acknowledged; zero root bytes needed
writing. Consequently this is **not** physical modified-root write or
filesystem-repair qualification. The normal return boot was independently
observed as `99ada76e-7490-40a1-89a6-280a687f2794`, kernel `6.12.62-v8+`.

The fresh sealed draft at `physical-owner-staging-20260926/` has acceptance pin
`29f2c1d57a2ebf6bdba3fb353e37f6a2549be69e67e87ce251b5a67a7ae65d56`.
Its `mcp-staging-acceptance.json` records real stdio JSON-RPC invocation of all
four owner-approved staging phases and an independent nine-file readback.
Staging changed only the selected alternate boot files; it did not reboot or
write the root image. The MCP server exited successfully. Owner approval is
separate from execution, binds an exact normal boot, and does not expand an
existing read-only client's grant. Reconciliation fences and observes a retained
attempt instead of repeating its write.

Enhanced native-root qualification is in progress in
`physical-enhanced-trial-20260926/`, using a frozen copy of that qualified owner
revision and a fresh offline whole-card backup. No enhanced-image, rollback or
final reimport pass is inferred from starting that job. Original compressed
backups, enhanced exports and validation evidence remain private, not release
assets. The redundant old `forge-enhanced-source-20260926/image.img` was removed
only after its SHA-256 matched the retained backup and no active readers were
found; its original compressed archive and final enhanced export remain.

### Live SSH application deploy/run/restore

`target-roundtrip-20260924/acceptance.json` records **passed** on the physical
CM4 via `jkh@clockworkpi.local`. The exact proof application previously tested
in the emulator/export was deployed from a durable, private host journal:
SHA-256 `466d89bc5df234e29786155732418c7085eb8cdf8e6c6e7121231b1afebc46d7`.
Its execution exited zero and printed its expected unique identifier. Restore
removed only that newly created application, and a fresh SSH capture confirmed
its original absence and the same machine identity.

Native `/boot/firmware/config.txt` and `cmdline.txt` hashes still match the
preimage capture. No reboot or service installation occurred. A root-owned 0600
runtime coordination file remains at `/run/lock/uconsole-forge-target.lock`;
the worker itself is not installed. Host backups/journals are private evidence,
not release assets. The 38 focused target tests include lost acknowledgement,
same-direction reconciliation, wrong-machine refusal and isolated worker loading.
This proves a live application round trip, not full-image boot, package/service
rollback, or complete hardware qualification.

### Physical reboot during live application development

`target-reboot-roundtrip-20260924/acceptance.json` records **passed** with
`validate_target_roundtrip.py --reboot`. The same application hash as the first
live round trip was installed and executed, then the physical CM4 was explicitly
rebooted. SSH reconnected to the same machine identity with a different boot ID
and systemd reporting `running` (seven observation attempts). The application
executed successfully after boot. Restore then removed the application and a
fresh capture verified the original absence. Native `config.txt` and
`cmdline.txt` hashes were unchanged across reboot.

The observer requires a new boot ID, matching machine identity and boot hashes,
and a running system; an SSH disconnect alone cannot pass. Tests cover transient
connection loss, old boot IDs, startup in progress, wrong machines, changed boot
configuration, malformed captures and timeout. This is live application/reboot
qualification, not proof that the exported whole image boots, that the native
desktop is visually correct, or that package/service rollback is implemented.

### Physical application round trip through real stdio MCP

`target-mcp-roundtrip-20260924/acceptance.json` records **passed** using the
validator's `--mcp` mode. A separate stdio MCP owner received a digest-pinned
target policy and the distinct `target-write` grant. Apply and restore both
completed through `target_transition` and `job_status`; their recorded contexts
match the approved plan and policy digests. The same emulator proof application
ran on physical CM4 and a fresh post-restore capture confirmed original absence.
No reboot was requested in this run.

The private output directory retains `mcp-transcript.jsonl`, owner stderr,
transaction journal and durable job history. The owner exited zero after its
accepted work finished. A separate real-owner test verifies that arbitrary host
overrides and ungranted guest execution are refused without dispatching SSH.
All 52 focused target tests pass. This is a deterministic protocol-client test,
not the outstanding independent coding-agent exercise, GUI target workflow,
service rollback or full exported-image boot qualification.

### Physical application round trip through Workbench buttons

`target-gui-roundtrip-20260924/acceptance.json` records **passed** using
`validate_target_roundtrip.py --gui` under Xvfb. The actual Workbench opened its
physical-target panel with an approved `gui` policy; the validator invoked its
Apply and Restore buttons. Confirmation dialogs were programmatically approved
for the exact inert fixture, not bypassed. `gui-apply.json` and `gui-restore.json`
retain the displayed plan summary, confirmation text, panel status, and completed
shared-controller jobs with their policy/plan digests.

The same proof application ran successfully on physical CM4. Fresh SSH capture
verified its original absence after restore. No reboot was requested. This is
an automated Tk/control-path test, not manual visual-usability qualification or
the still-pending interactive backup/transaction authoring flow.

After adding this route, the full repository suite passed under Xvfb:
`xvfb-run -a /usr/bin/python3 -m unittest discover -s tests` ran 434 tests
in 82.548 seconds, with final result `OK`. Deliberately failed validator fixtures
and simulated flashing output are negative-path unit tests, not extra physical
device actions or physical qualification results.

### Read-only transaction authoring on the physical target

`target-author-20260924/review.json` records `prepared-for-review` from the new
`forge_target_prepare.py` authoring command using the existing emulator proof
artifact. It captured the physical destination's absence, froze the 74-byte
application at its previously qualified hash, and created a root-owned 0755
desired state in the host journal. Plan SHA-256:
`64358309ad83d094f434ab99d40cea4e87a6b47e504cb491ca4baec0155fdd78`.
No deployment was performed. Six authoring tests cover frozen source bytes,
preserved metadata, invalid mappings, duplicate targets, privileged-file refusal
with backup retention, and exclusive output directories.

### Full physical GUI authoring round trip without preloaded policy

`target-gui-author-roundtrip-20260924/acceptance.json` records **passed** with
`validate_target_roundtrip.py --gui-author`. Workbench started without a target
policy. Its actual Prepare button collected the inert local artifact and target
mapping through programmatically answered dialogs, then a shared-controller job
captured the physical preimage and generated the review. The validator checked
that preparation had not granted `target-write`, invoked the real Approve button,
then invoked Apply and Restore. The application ran successfully on CM4; fresh
SSH capture verified its original absence after restore.

`gui-author.json` retains the backup/approval dialog text, prepared review,
completed preparation job and generated policy digest. `gui-apply.json` and
`gui-restore.json` retain subsequent confirmation text and completed jobs. This
qualifies the automated no-preloaded-policy authoring/control path for one new
application file. It does not establish manual UX quality, existing service or
package rollback, physical whole-image replacement, or reboot recovery after
boot-breaking changes. No reboot was requested in this run.

### Passive physical service-state capture

`target-services-20260924.json` records two stable consecutive observations on
the physical CM4. `cron.service` is loaded, active/running and enabled, using
`/usr/lib/systemd/system/cron.service` without drop-ins. The proposed
`uconsole-forge-live-proof.service` is not found, inactive/dead, with no fragment
or enablement state. Neither unit is transient or needs daemon reload.

The probe issues only `systemctl show`; it does not change either unit. Seven
focused tests cover stable capture, absence versus inactive-loaded state,
changes between reads, aliases/transient/reload/transition refusals, malformed
properties, invalid names and exclusive private host output. These properties
are a service-state preimage, not unit-content backup or service rollback proof.

### Physical mutable service-link capture

`target-services-links-20260924.json` adds a stable two-pass inventory of mutable
system-unit links to the service properties. The selected cron service has
`/etc/systemd/system/multi-user.target.wants/cron.service` pointing literally to
`/usr/lib/systemd/system/cron.service`; uid/gid and link timestamp are retained.
The proof service has no selected links. Both mutable roots exist. No service
or symlink was modified. Twelve focused tests cover passive properties plus
literal link targets, alias chains, missing/empty roots, linked-directory refusal,
capture changes and malformed link records. This is not full dependency-effect
qualification or service rollback evidence.

### Live-target preimage capture

`target-backup-20260924/preimages.json` was captured over authenticated SSH as
`jkh@clockworkpi.local` with passwordless sudo. It contains verified preimages
for native `config.txt` and `cmdline.txt` under `/boot/firmware`, plus absence
records for the proposed application and proof service. SHA-256:
`3627362b60a72475e2cc6b922b11973384d476731671e45cb2d3a129e94830c1`.
The file is private and not a release asset. No deployment, reboot or restore
was performed by this check; the two native boot files were read, not changed.
Eight focused tests cover preimage capture and host artifact validation. This
establishes the backup foundation, not completed backup-and-restore acceptance.

### Physical versus maintenance-guest subsystem comparison

`build/emulator/subsystem-guest-20260924/inventory.json` records a completed
capture against physical reference `hardware-clockworkpi-20260924-r4.json`.
Status is **captured**, not a hardware-equivalence pass. Both captures have no
attribute-read errors. The guest shut down without forced cleanup; its root
primary ext4 superblock is clean with valid checksum (not a full filesystem
check), and the stock backing-image hash is unchanged.

Comparison normalizes USB bus/device enumeration numbers and DRM card numbers,
but preserves USB port paths, multiplicity, driver bindings and device fields.
Malformed or partial captures cannot match. Observed differences include:

* Physical framebuffer: VC4 DRM, 720x1280, 16 bpp; surrogate: BCM2708 mailbox,
  1280x720, 32 bpp. This does not establish user-visible rotation equivalence.
* Physical carrier hub: 05e3:0608, 480 Mbps, four ports; QEMU hub: 0409:55aa,
  12 Mbps, eight ports. Keyboard HID/CDC interfaces and the captured keyboard
  identity fields match, but the carrier hub is not faithful.
* Physical backlight, Bluetooth, DRM connectors and rfkill classes are absent
  in this maintenance capture. The guest thermal class is empty; the physical
  target reports a CPU thermal zone. Maintenance boot does not load all normal
  services/drivers, so absence here alone is not a normal-boot capability test.
* Both have CDC ACM; the physical auxiliary serial entry is ttyS0, whereas the
  guest console is ttyAMA1.

Reproduce with `tools/validate_hardware_inventory_guest.py --image RAW
--sha256 HASH --reference CAPTURE.json --output NEW_DIRECTORY`. This creates a
small owned overlay and retains the probe and detailed comparison. It does not
modify the physical reference target.

### Expanded physical CM4 subsystem inventory

The passive hardware probe now captures structured framebuffer/DRM, backlight,
rfkill, Bluetooth, serial, thermal and USB sysfs fields, plus USB topology, ALSA
cards/PCMs and block-device layout. It reads fixed allowlisted attributes, does
not scan I2C addresses, read framebuffer pixels, pair radios, play sound or
change hardware state. Missing classes are explicit; attribute read failures
retain partial data and return a failing probe status. USB serial numbers are
not included in the new subsystem capture. Raw older inventory fields may
still contain identifiers and must be reviewed before publication.

All 16 probes succeeded on the physical CM4 in
`hardware-clockworkpi-20260924-r4.json`, captured at
`2026-09-25T03:56:07.235664+00:00`. Four new subsystem tests pass; the 28 existing
emulator tests also passed with the expanded probe. Observations include:

- `vc4drmfb`: 720×1280, 16 bits/pixel, stride 1440; DSI advertises 720×1280.
- OCP8178 backlight: maximum 9; the earlier r3 snapshot showed requested 3,
  actual 0 and blanked power state. These are sampled states, not a fault claim.
- bcm2835 headphone playback plus two HDMI playback devices; no capture PCM
  was listed in this snapshot.
- Bluetooth through `hci_uart_bcm`; WLAN and Bluetooth rfkill states unblocked.
- DWC2 root hub → four-port USB hub → composite keyboard with HID/CDC drivers.
- One persistent 31,914,983,424-byte system disk with mounted boot/root
  partitions; no spare persistent boot device was visible.

The emulator's 1280×720/32-bit surrogate is therefore not the physical DSI
scanout contract. Panel rotation, DSI timing, brightness behavior, audio and radio
operations require additional qualification; this inventory does not prove them.

### Firmware input in an actual desktop terminal

The Workbench desktop recorder now supports `--keyboard composite` and explicit
`firmware`/`firmware-keys` actions through the shared controller. Generic QEMU
key/type/move/click requests are rejected in this mode. Its chord helper has
tests for scanner settling, modifier release and invalid input.

A small overlay of the retained enhanced image was booted in
`keyboard-desktop-20260924`. The actual QEMU command contains only the composite
keyboard transport, not generic keyboard/mouse devices. After the desktop
finished starting, firmware Ctrl+Alt+T opened a terminal. Firmware matrix
press/release actions typed `echo forgeproof` and Enter; the captured terminal
visibly showed both that command and its `forgeproof` output. Firmware input
then typed `sudo poweroff` and Enter. Systemd reached poweroff, the kernel logged
`reboot: Power down`, and Workbench reported exit 0. The recorder finished with
no forced cleanup, and stopped primary root-superblock checks passed. The
enhanced backing image retained SHA-256
`c817ffd8ac00f0e2bb57ae57b67c29a9b8074c81f17c7082481e0497a5bbdc7a`.

Evidence directory:
`keyboard-desktop-20260924/desktop-record-2b32d383310747609695a196bf7c3642/`.
The inspected command/output screenshot is
`screen-505c9dc8e9664f67af9d4ef27d019c0f.png`; `record.json` and `console.log`
retain actions and shutdown. This is visually inspected desktop keyboard
evidence, not an automatic recorder pass, physical-host typing, pointer-target
qualification or hardware-image boot. Recorder secrets remain private and
must not be published with evidence.

### Explicit Select-scroll through production firmware

The GUI pointer validator also passes with `--select-scroll`, retained in
`keyboard-scroll-linux-20260924/acceptance.json`. Nine GUI jobs cover motion,
up/down wheel input, three mouse buttons and focus-loss release. Linux received
wheel values +1 and -1, plus two Space press/release pairs from production
Select behavior. The acceptance verifier requires those side effects rather
than filtering them. The GUI defaults this feature off and labels its effect;
held contacts prevent starting a scroll action. The guest stopped cleanly with
no forced cleanup and unchanged base, and primary root-superblock checks passed.

The real firmware oracle also passes report-byte tests for both wheel signs
and Space press/release. Native host wheel capture, game mode and physical
keyboard behavior remain unqualified; this test uses generated X11 Tk events.

### Focus-scoped pointer input into the stock Linux guest

`validate_keyboard_guest.py --oracle build/keyboard-oracle/keyboard-oracle --gui
--pointer` passed under Xvfb, retained in
`keyboard-pointer-linux-20260924/acceptance.json`. Generated Tk motion traveled
through the focused pointer pad, bounded edge adapter, production trackball
firmware and composite USB transport. Linux evdev recorded positive relative X/Y
movement and ordered left/middle/right press/release pairs. The final right
button release came from moving focus away, not an explicit release event.
All seven GUI jobs completed with no retained host contacts or pending releases.
The guest stopped without forced cleanup; primary root-superblock checks passed
and the base image hash was unchanged. Nine acceptance-guard tests and nine
focused GUI tests passed, including rejection of missing-axis and out-of-order
button evidence.

This qualifies generated Tk events into Linux input, not physical host mouse
capture, desktop click targets, pixel-perfect motion or wheel input. Production
acceleration and virtual scan timing are intentionally retained.

### Focus-scoped host typing into the stock Linux guest

`validate_keyboard_guest.py --oracle build/keyboard-oracle/keyboard-oracle --gui
--host-typing` passed under Xvfb, retained in
`keyboard-host-linux-20260924/acceptance.json`. The validator focused the actual
typing canvas and generated Tk A press/release and F1 press events, then moved
focus to the deck window. Four controller jobs produced ordered Linux A/F1
press/release events. Focus loss released both the F1 character contact and its
synthesized Fn contact; the host mapper, queue and deck ended with no holds.
The guest stopped without forced cleanup, the stopped primary root superblock
was clean/checksum-verified, and the backing image hash was unchanged.

These are generated Tk events through the actual bindings, not physical host
keyboard capture or desktop application qualification. Non-US layouts, all
mapped symbols, high-rate typing/backpressure and pointer mapping still need
broader acceptance coverage.

### Linux Caps Lock LED feedback into firmware reports

`validate_keyboard_guest.py --oracle build/keyboard-oracle/keyboard-oracle
--caps-feedback` passed in the stock maintenance guest. Evidence is retained in
`keyboard-led-linux-20260924/acceptance.json`. Firmware Fn+Tab generated Caps
Lock, Linux sent LED value 2 through the USB output path, and the bridge sampled
that state before pressing A while Caps remained held. Its report was exactly
`020200390400000000`: Shift adjustment, held Caps, and A. No host QOM LED setter
or synthetic guest LED write was used. Linux observed Caps, Shift and A
press/release events. The guest stopped cleanly without forced cleanup, the
primary root superblock passed its checks, and the base image was unchanged.
Seven acceptance-guard tests pass, including rejection of correct bytes with
the wrong sampled LED or correct LED with wrong report bytes.

This qualifies one live guest LED round trip, not all LED combinations, physical
keyboard timing or desktop application behavior. The held-Caps adjustment is
the production firmware's current behavior, not a new host-side key mapping.

### Tk deck buttons through the stock Linux guest

`validate_keyboard_guest.py --oracle build/keyboard-oracle/keyboard-oracle --gui`
passed under Xvfb, with retained evidence at
`keyboard-gui-linux-20260924/acceptance.json`. The helper invokes actual Tk deck
buttons and waits for each shared-controller job and GUI state update. Six jobs
exercise A press/release and Fn-selected F1, releasing Fn before F1. Linux evdev
transitions are independently checked in order. The deck ended with no held
contacts and closed successfully. Guest HID/CDC driver bindings matched; clean
shutdown and root-primary-superblock checks passed without forced cleanup, and
the backing image remained unchanged.

This validates the deck widget/controller/oracle/USB/Linux path, not the complete
Workbench boot UI, host key/pointer mapping, desktop application behavior, game
mode or trackball motion. The validator owns its runtime throughout; it does not
discover or adopt external VMs.

### Firmware input through a real MCP server

`validate_forge_mcp.py --workspace build/emulator/keyboard-mcp-20260924 --keyboard`
passed with a real stdio server process and stock Linux guest. The client
selected composite input at boot, uploaded a bounded input observer, then sent
six `keyboard_input` jobs. Linux reported ordered A and Fn-selected F1
press/release events, including Fn release before F1 release. HID/CDC drivers
matched the contract. The exact probe files were removed, the guest stopped
cleanly, the primary root superblock passed its checks and the backing image
hash remained unchanged. After server exit, a no-grant reconnect read the same
durable job results without adopting a VM or gaining cancellation authority.

The passing transcript is
`keyboard-mcp-20260924/mcp-client-2b4453a6a7f34e88993455d69c5c04fa.jsonl`.
An initial observer-readiness failure is retained with its transcript and
`serial-first-observer-failure.log`. The launcher now uses synchronous
`Popen(start_new_session=True)` creation before the guest job exits, preventing
the background-launch race with job process-group cleanup. This is a scripted
MCP client test, not independent coding-agent or GUI-input qualification.

### Packaged firmware oracle discovery

`make build` now compiles the native firmware oracle into the Workbench stage
at `libexec/uconsole-workbench/bin/keyboard-oracle`. On Linux ARM64, stage
`build/ide/linux-aarch64/build.iLRzP1/uconsole-workbench` was tested from `/tmp`:
the staged Python module found the bundled executable, which emitted A
press/release firmware events and acknowledged a sync token. The staged MCP
launcher exposes the owner-only override. Discovery prefers the bundled binary,
then the configured source build; it never searches a client workspace or PATH.
Thirteen bridge/controller/discovery tests and ShellCheck passed. This is a
build-stage check, not final archive/install or cross-platform qualification.

### Physical CM4 inventory and persistent keyboard bridge

The passive SSH capture at
`build/emulator/hardware-clockworkpi-20260924.json` completed all 11 probes
successfully on `clockworkpi.local`: Raspberry Pi Compute Module 4 Rev 1.1,
ARM64 Linux 6.12.62-v8+. The physical keyboard enumerates as 1eaf:0024 with
ClockworkPI/uConsole/20230713 strings, three interfaces, a 100-byte
configuration and a 219-byte HID report descriptor length. Its listed HID/CDC
endpoints match the modeled allocation. `lsusb` could not retrieve the report
descriptor itself; this inventory does not establish report-byte or behavioral
equivalence. No reboot, flashing or hardware configuration change was performed.

The follow-up `hardware-clockworkpi-20260924-r2.json` adds an owned-device
sysfs descriptor capture; all 12 probes succeeded. Unlike the earlier `lsusb`
output, this includes the HID report bytes cached by the kernel. The physical
118-byte device-plus-configuration stream matches the stock emulator guest's
`keyboard-linux-20260924-r2/acceptance.json` byte-for-byte (SHA-256
`e469f13d5ff99c7ea8e036025997e9c98acfde44a668577b245969868a50c5c9`).
The physical 219-byte report descriptor matches the compiled firmware ELF
exactly (SHA-256
`351629b59fd36f6dc0b2aea094a207e232e668a0eff8a5bb38caa2d9a7c297b7`).
Three capture tests cover ownership, missing bytes and missing/ambiguous devices.
This is descriptor-level evidence for this particular physical device, not a
firmware-version identification, raw key-report capture or timing qualification.

The new `forge_keyboard.KeyboardBridge` keeps one firmware oracle process and
report encoder per explicitly owned composite runtime. Ten focused tests pass,
covering persistent Fn identity, sampled LED feedback, invalid commands,
uncertain delivery, queue deadlines, owner/reset changes, concurrent calls,
child-only cleanup, malformed oracle output and short writes. Transport tests
use a fake owned runtime; live guest and GUI/controller integration remain open.
The full suite passed 328 tests under Xvfb before the two new protocol tests;
the separate focused run includes both additions. The keyboard patch passes
`git apply --reverse --check --directory=build/emulator/qemu-10.2.4` against
the current build source.

### Firmware-derived HID encoding and DMA replay

The persistent bridge subsequently passed a real stock Linux guest run,
recorded in `keyboard-bridge-linux-20260924/acceptance.json`. Six actions through
one oracle process produced A and F1 press/release events; Fn was released
before F1, retaining the original selected key identity. The stricter ordered
transition verifier was also applied successfully to the retained observations.
The guest stopped without forced cleanup, the primary ext4 superblock was
clean/checksum-verified, and the backing image hash was unchanged. This profile
does not qualify LED changes, pointer/gamepad desktop interaction or real-time
behavior. The separate raw-report profile remains available and unchanged.

The new encoder was compared with the pinned core's actual Keyboard, Mouse,
Consumer and Joystick C++ method bodies compiled against a host send/layout
shim. All 6,054 deterministic input events produced the same 5,569 reports,
including overlapping keys, rollover, Caps feedback, button transitions and
axis clamping. The run also passed with undefined-behavior sanitization.
Evidence with source/shim/encoder hashes is retained in
`build/emulator/keyboard-report-differential-20260924.json`; report-stream hash:
`d554a7b700d65ad195f73d7b5f19a6167e0f4e363dc5e0031e1a67bb4956923a`.

`make check-emulator` passed with the added firmware trace replay: 17 reports
from actual matrix, Fn, game-button and trackball actions passed through the
encoder and were read unchanged via QEMU DWC2 DMA. All four report IDs were
covered. The stdin CLI pipeline also produced the expected A press/release and
initial joystick reports. All 51 keyboard-focused tests passed.
This establishes ordering/encoding, not real-time pacing, live host input,
ongoing LED feedback, desktop interaction or physical comparison. No guest disk
or physical keyboard was modified by these checks.

### Composite keyboard in the stock Linux guest

`validate_keyboard_guest.py` passed twice against the hash-pinned stock base,
with retained evidence in `build/emulator/keyboard-linux-20260924/acceptance.json`
and `build/emulator/keyboard-linux-20260924-r2/acceptance.json`. The second run
uses the stricter host-side event/driver checks and clean QEMU exit check.
Both runs use new small overlays; the backing image hash remained unchanged.

The stock guest bound `usbhid` to interface 0 and `cdc_acm` to interfaces 1/2,
exposed `ttyACM0` and four evdev nodes, and reported the expected manufacturer,
product and serial. CDC termios changed/read back at 9600 baud. Seven injected
HID reports produced verified A press/release, relative X/Y/wheel, volume-up
press/release, joystick button press/release and absolute X/Y events. No required
events were missing. Both guests shut down without forced cleanup; stopped root
primary ext4 superblocks were clean/checksum-verified (not a full fsck).

The CLI now has opt-in `--keyboard composite`, rejecting the conflicting split
CDC surrogate. Generic input remains the default. All 28 emulator unit tests and
41 keyboard-focused tests passed; the full 310-test suite also passed under Xvfb.
Negative validation cases cover a bad
base hash, absent event evidence, incorrect driver binding and readiness failure.
No packages or permanent guest services were added; the exact UUID-named guest
probe was removed after success. This qualifies the Linux driver/input boundary,
not firmware-driven input mapping, desktop interaction, DFU or physical fidelity.

### Experimental composite keyboard USB transport

The pinned QEMU build now includes opt-in `usb-uconsole-keyboard`. The complete
`make check-emulator` gate passed: 27 emulator unit tests and the watchdog,
PMIC, GPIO, firmware GPIO, GIC, existing USB-wakeup and new keyboard qtests.
The new test communicates through CM4 DWC2 DMA, not a direct device callback.
It verifies the full 100-byte configuration, device/string/report descriptors,
all four input report IDs, queue order/capacity and malformed-input rejection,
LED output, line coding/interface scoping, empty/full endpoint NAKs, bus reset,
and independent magic/1200-baud CDC reset-request detection. An initial test
exposed retained USB configuration after bus reset; reset now clears both
configuration and queues, and the expanded test passes.

The build succeeded with the new tenth patch, and its exact reverse dry-run
matches the build source. Generic Workbench input remains the default.
Reset requests are diagnostic flags only; firmware state-machine integration,
Linux driver binding/desktop input, DFU transitions and hardware comparison
remain unverified or unimplemented. No image was modified by these qtests.

### Compiled keyboard USB transport templates

`keyboard_usb_contract.py --usb-templates` now extracts four additional named
ELF objects, validates their sizes and descriptor boundaries, and records their
bytes/hashes without applying guessed runtime substitutions. The actual local
firmware extraction succeeded and is retained at
`build/emulator/keyboard-usb-templates-20260924.json`; ELF SHA-256 is
`df83700173c1fc180d59c8b01b710dc91e244c55af0f3c87f61edb6de5525c67`.
The device/configuration-header/HID/CDC templates are 18/9/25/66 bytes.
The HID report descriptor remains 219 bytes with its previously recorded hash.

Regression tests cover optional inclusion, ELF identity, exact template bytes,
missing symbols, wrong sizes and invalid descriptor boundaries. The evidence
explicitly says runtime initialization and hardware capture are false: zero
endpoint numbers and serial index in the templates are startup placeholders,
not a claim about the enumerated keyboard. Source inspection also identified
the pinned core's DTR/1200-baud and DTR-plus-`1EAF` CDC reset requirements; the
corresponding hooks are present in the compiled ELF. Composite USB transport,
runtime descriptor capture and DFU behavior remain to be implemented/qualified.

### Keyboard firmware behavior oracle

`make keyboard-oracle` builds a host executable that includes the production
firmware sources unchanged. Logical GPIO, virtual time and USBComposite API
recording allow direct scanner/layer/trackball regression testing without an MCU.
The build emits two existing firmware unused-variable/parameter warnings.

All 32 keyboard-focused tests passed, including 14 new oracle tests. The 14
also passed with `-fsanitize=undefined -fno-sanitize-recover=all`. Coverage
includes matrix and direct-key debounce, press/hold/release, Fn identity,
keyboard lock, Caps adjustment, all 17 direct keys, consumer controls, game
buttons, backlight levels, and deterministic trackball movement/Select scrolling.
The actual D-pad map emits arrows even with PD2 low; the oracle records this
rather than substituting the joystick behavior suggested by older comments.

The 14 oracle tests also passed with Clang on Linux ARM64 using
`CXX='clang++ --gcc-install-dir=/usr/lib/gcc/aarch64-linux-gnu/13'`.
The initial unqualified Clang invocation failed because it selected the installed
GCC 14 directory without C++ headers; selecting the existing GCC 13 headers fixed
that host-toolchain issue. This does not qualify macOS or another architecture.

This is a semantic reference for M4, not a completed composite device: no USB
packet encoding, rollover, CDC, DFU, physical capture or QEMU guest enumeration
is established by these tests. See [oracle usage and limits](keyboard-usb-contract.md).

### Long-running controller and attachment sessions

The 128-entry controller cache now retires only terminal jobs whose history
commit succeeded and whose cleanup finished. Retired outcomes remain readable;
active work, failed persistence and history-free results are preserved rather
than silently evicted. Polling and cancellation snapshot live bookkeeping under
the controller lock, and attached clients bound their cancellation-ID sets.

All 120 forge-focused tests and the full 286-test suite passed under Xvfb.
New regression cases submit 140
jobs through one controller and through one attached client, verify historical
results/context and read-only cancellation rules, preserve active jobs and
failed writes, refuse overflow without history, and retire durable failed and
cancelled outcomes. These are controller/transport-boundary tests, not a new
guest, physical-hardware or release qualification run.

### Agent guidance and staged Linux ARM64 bundle

The companion skill now distinguishes standalone ownership from restricted live
attachment, with mode-specific setup in `references/connection-modes.md`.
Instructions cover per-connection cancellation, reconnect/history limits,
conditional desktop preparation, pause/resume readback and private image exports.
The skill validator passes for both the source and freshly staged copy.

`make build` succeeded and retained the stage at
`build/ide/linux-aarch64/build.sr4nnP/uconsole-workbench`.
Its copied connection reference matches the source byte-for-byte. The actual
`uconsole-mcp` launcher exposes `--connect`, and the `uconsole-workbench` launcher
run from `/tmp` exposes the opt-in attachment arguments. This checks stage
content and launcher resolution, not a fresh firmware-containing archive install,
end-to-end staged guest execution, or the other advertised host platforms.

### Attached application/export and raw-image boot-back

`validate_forge_attachment_gui.py --agent-boot --agent-image-export` passed:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-attachment-402820a5a1694c878c6686b259fa77d4/record.json`.
The attached client installed and tested
`/usr/local/bin/attached-forge-18d1f70839a3472ca86636ade753bcb9`, cleanly stopped
the GUI-owned guest, refreshed boot artifacts, and exported `files/enhanced.img`.
The application intentionally remains in this validation image and source
workspace. Native `config.txt`, `cmdline.txt`, `kernel8.img` and CM4 DTB hashes
match the immutable imported base. Export SHA-256:
`c817ffd8ac00f0e2bb57ae57b67c29a9b8074c81f17c7082481e0497a5bbdc7a`.

`validate_exported_application.py` then booted that exact raw export through a
small disposable writable overlay, verified the installed application's digest
and output, and cleanly stopped it. Evidence is in the same directory under
`export-bootback-r3/acceptance.json`. The raw export hash remained unchanged and
the stopped overlay's primary ext4 superblock was clean/checksum-verified.
This avoids another 6.8 GiB base copy; it is **boot-back**, not qualification of
the normal importer's copy path or physical hardware.

Two earlier boot-back attempts remain: the first encountered kernel messages
inside raw command output, and the second exposed the same issue in the guest
supervisor's directory bootstrap. Both used forced failure cleanup in disposable
overlays. The production bootstrap now requires one per-request framed directory
reply, tolerates surrounding kernel messages, and rejects missing/ambiguous
markers before upload or cleanup. The successful third run used supervised
output capture. Regression tests cover those cases plus fixture-metadata refusal
and attempted clean cleanup on failed output checks.

The export is private test data containing prior onboarding/test accounts; do
not distribute it as a release image. This deterministic protocol-client test
does not substitute for an independent coding-agent exercise or device boot.
Approximately 7.5 GiB disk headroom remains; no retained images were deleted.

### Agent-initiated boot into Workbench

The attachment validator's `--agent-boot` mode starts with an idle Workbench and
grants the attached client `boot`, `force-stop`, `guest-exec` and `transfer`.
The client remains unable to configure desktop adapters without `image-write`.
It boots maintenance mode through MCP, waits for GUI serial readiness and checks
that the boot result's runtime identity exactly matches the GUI-owned runtime.
It then repeats binary transfer, live guest cancellation, peer observation and
denials, channel reuse, disconnect, and GUI clean shutdown.

The complete real path passed with the identity check:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-attachment-616b7d907d534cfd86e60a6f3c5e5f96/record.json`.
An earlier pass before adding explicit identity comparison is retained at
`gui-attachment-ee0700f9064144f4bbc4bd5310804940`. A new Tk regression also verifies
that agent boot completion exposes the exact controller-owned Runtime rather
than creating or discovering another VM. The three peer-policy verifier tests
still pass. This covers attached maintenance boot, not attached normal/desktop
boot or image export/reimport/physical deployment qualification.

### Attached transfer, cancellation and peer exclusion

The expanded real GUI-owned VM acceptance passed with separate stdio clients:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-attachment-3d74bb499ac1440293fb99256d413855/record.json`.
It granted only `guest-exec` and `transfer`, rejected an upload path outside the
approved files root, and round-tripped 1024 binary bytes containing all byte
values. SHA-256 was
`785b0751fc2c53dc14a4ce3d800e69ef9ce1009eb327ccf458afe09c242c26c9`.

After observing supervised guest process group 345, a second client read the
running job but was denied both cancellation of another session's job and
conflicting guest execution. The submitting client then cancelled the command;
its follow-up verified the process group no longer existed, checked the guest
fixture digest, removed only that fixture, and demonstrated serial-channel
reuse. Source/downloaded host copies remain in the evidence directory. Client
disconnect preserved the same GUI-owned VM. GUI clean shutdown completed with
a clean, checksum-verified ext4 primary superblock and no fallback forced cleanup.

Three verifier tests require live observation and the specific policy denials,
rejecting unrelated errors or accidentally successful mutations. The retained
real peer transcript also passes that stricter verifier. An earlier expanded
transfer/cancel-only pass is retained at `gui-attachment-7b4a36cfb388437a84a0030a35724b29`.
These checks do not qualify attached image/boot operations, the complete external
coding-agent edit/test/export loop, physical hardware, or other supported hosts.

### Live Workbench attachment

Workbench now exposes an opt-in `--agent-socket`, with read-only default sessions,
explicit `--agent-allow` grants and optional `--agent-files-root`. Authorized
submissions cross a bounded owner-thread queue; guest jobs release the GUI serial
connection, and GUI actions/reconnection/closure wait for terminal cleanup.
Unit tests cover abandoned queue requests, owner-thread execution, close wakeup,
denial without touching serial and accepted-job guards through client disconnect.

Real Tk/QEMU plus a separate stdio adapter process passed:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-attachment-dd25b066c42a4a149d3d8aff625c60f6/record.json`.
The GUI booted maintenance mode. The client had only `guest-exec`, was denied
boot, inspected the live owner, and received `attached-agent-proof` from a
supervised guest command. After client disconnect, the same GUI-owned process
remained alive and serial reconnected. GUI clean shutdown completed and the
stopped ext4 root had a clean, checksum-verified primary superblock. No fallback
forced cleanup was needed. This is not yet attached transfer/image/boot/cancel
acceptance, an external-agent edit/test/export exercise, or host-matrix coverage.

### Stdio attachment adapter

`uconsole_mcp.py --connect SOCKET` now forwards stdio to the private local
transport without constructing a controller or granting authority. Four added
real-process tests exercise initialization/resource reads, a request/reply larger
than one transport chunk, denied mutation, endpoint symlink rejection, rejection
of owner-configuration flags, and owner disconnect while stdin remains open.
All eleven local-transport tests pass. These are socket/controller tests, not
GUI/VM attachment acceptance; Workbench serial coordination is still required.

### Private local transport foundation

`forge_local.LocalListener` serves restricted sessions through a private Unix
socket without taking ownership of the controller. Seven transport tests cover
owner-only permissions, existing/replaced endpoint preservation, rejected public
or symlink directories, idle timeout, client limits, rejection of accidentally
passing the owning controller as a session, and disconnect semantics. A separate
Python client process completes MCP initialization, reads its approved workspace
and is denied boot despite the owner having boot authority. Two socket clients
also demonstrate live job observation without peer cancellation or cancellation
on disconnect. These tests use an actual socket and controller executor/history,
but no VM. Workbench does not expose a listener yet; GUI serial coordination
remains required before real attached-agent use. Stdio adapter evidence is above.

### Restricted client-session foundation

`forge_client.ClientSession` is an internal authorization facade for future
attachment to one owning controller. Six
tests cover non-inherited grants/file access, narrower file-root enforcement
(including symlink escape), workspace-scoped live/history reads, per-client
cancellation authority, shared workspace exclusion, preservation of accepted
jobs after disconnect, and MCP resource/tool scope enforcement. The concurrency
test uses the real controller executor/history with a blocked fixture operation,
not a VM or separate client process. These tests alone do not qualify transport
or GUI serial coordination; separate transport evidence is recorded above.

### Shared pause/resume jobs

Workbench and MCP now use the same permission-scoped `pause`/`resume` jobs.
Tests cover default denial, exact owned commands, before/after state readback,
failure without rollback, off-thread GUI execution, conflict/closure guards
and durable history. These are not clean shutdown or checkpoint operations.

Real GUI pause/resume followed by the power/replay/shutdown workflow passed:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-client-b03b8146ef9a415f98d76b95814e7b22.json`.
The pause job recorded running to paused, and resume recorded paused to running,
with the expected boolean readbacks. Guest operations continued after resume.
This closes the direct GUI pause/resume path; it does not provide cross-client
VM attachment, physical hardware equivalence or final host qualification.
The 251-test Xvfb suite passed, plus the separately added GUI readback-guard
regression. Separate stdio MCP pause/resume, power/replay, clean stop and
read-only reconnect also passed, with transcript
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/mcp-client-16063a6899c54875a6bd6c3f8b3b8c1d.jsonl`.

### Shared single power operations

GUI power queries/changes now submit the same controller jobs as MCP rather
than running QMP on the Tk thread. The shared worker retains per-operation
JSONL evidence and durable history, including the requested change and evidence
path. A failed readback records failure without implying rollback. GUI tests
cover validation before submission, off-thread work, conflict/closure guards,
refusal of running cancellation and retained success/failure evidence.

The real power/profile/replay/shutdown workflow passed:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-client-10d863246aa9447cb7e2b142e58c35be.json`.
It records completed `power_query`, `power_set`, `power_query` jobs, guest
readback of the changed AC state, and clean controller shutdown. The acceptance
driver waits synchronously for job results for assertions; the production GUI
returns immediately and renders results from its Tk polling loop. Pause/resume
and live cross-client coordination remain separate integration work.
The complete Xvfb suite passes 249 tests. Separate stdio MCP acceptance also
passes power snapshots, driver IRQ replay, cancellation with preserved effects,
clean stop and read-only historical reconnect:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/mcp-client-a8edf9a5549e4b53888179a5fa7bad63.jsonl`.

### Shared GUI power replay

Workbench now submits the same controller replay job as MCP, replacing its
separate executor. Tests cover worker-thread execution, immutable schedule
selection, refusal of another controller operation while replay owns the
workspace, cancellation, GUI guards and durable cancelled-job context. The
record includes the schedule digest, event count, host-monotonic clock and
retained event-log path.

Real power-profile/live-change/replay/cancellation/clean-shutdown acceptance
passed with this implementation:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-client-8bb1730bbe0b4b4ca7cdee7294ef6e7c.json`.
The guest still reported the applied AC-off state after cancellation, confirming
that completed effects were not rolled back. No fallback forced cleanup was
needed. Single live power queries/changes and pause/resume are still separate
GUI paths; this is not full control parity or live cross-client attachment.

### Shared asynchronous GUI boot

Workbench boot now uses `Controller.submit_boot`, as does MCP. The GUI remains
responsive during boot-artifact refresh and shell readiness, exposes **Cancel
boot**, snapshots launch settings, and retains guards until cleanup completes.
A failed initial launch cleanup preserves the exact Runtime ownership rather
than losing a still-live child. QMP readiness is retried for normal/desktop boots.

The 247-test Xvfb suite passed, followed by an additional focused initial-cleanup
ownership regression. Real Linux ARM64 evidence:

- The maintenance transfer/guest-command/shutdown workflow passed after async
  boot: `build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-transfer-dcfca139d6fc43669e52ae2f9cf4a3f9/record.json`.
- Live GUI boot cancellation on the disposable cancellation overlay passed:
  `build/emulator/desktop-preparation-live-20260924-r2/cancel/gui-transfer-0229e85754854297bbb48ed5f89f44eb/record.json`.
  QEMU PID 4114272 was observed live before cancellation; job
  `6f9b725c7dfa43fb93cac2611c5afec4` recorded cancellation, the owned child exited,
  and the workspace lock was released. Forced power removal is explicitly
  recorded, not represented as a clean shutdown or rollback.
- Initial power profile, live changes, replay/cancellation and controller clean
  shutdown passed: `build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-client-24075d1d70ea4c70a8b02a55ffb31ec3.json`.
  The two preceding validator failures (`ce12345c0b4c4680bae7147b6254fda0` and
  `cb559ca0521e49a09196b865280cb72b`) retain evidence of stale synchronous-boot
  and old console-marker assertions; the validator now waits for boot jobs and
  checks the shared shutdown job result.

These checks do not qualify normal/desktop GUI boot on every supported host or
provide live cross-client attachment. Power/replay still has separate GUI paths.

### Shared desktop preparation jobs

Workbench's automatic desktop preparation now uses the same cancellable image
job controller exposed as MCP `configure_display` (requires `image-write`).
Tk tests cover successful continuation, failure/cancellation without an automatic
boot, and retention of GUI guards until cancellation cleanup finishes. MCP tests
cover default denial and routing through the shared image job.

The real image-job/history validator passed with `--operation configure-display`:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-image-ed08110f8b3c42ed861c9e2cdb4bf970/record.json`.
The existing workspace was already configured, so preparation correctly did no
guest writes. A separate read-only MCP process retrieved the completed GUI job
and refused an ungranted preparation request. This verifies the real subprocess,
history and permission path, **not a fresh adapter installation or live setup
cancellation**. The subsequent fresh-overlay acceptance below covers those
paths. Earlier interactive desktop evidence below remains separate.

`tools/validate_desktop_preparation_gui.py` subsequently passed on Linux ARM64
with the pinned stock raw base (`a7b0a2bfa86a45150af1ae70a94ed432718bfec90ffdef54952564bd34f0d788`).
It creates small independent writable overlays with read-only raw backing, not
another full base-image copy. Evidence is retained in
`build/emulator/desktop-preparation-live-20260924-r2/{complete,cancel}/acceptance.json`:

- Fresh preparation completed through the actual GUI/controller/subprocess/guest
  path, published schema 16, and reached the GUI launch continuation exactly once.
  The validator intercepts that final launch; it does **not** boot a desktop.
  The stopped root had a clean, checksum-verified ext4 primary superblock.
- Cancellation was requested only after observing the live setup launcher and
  QEMU in its owned process group. The job became cancelled, the group had no
  remaining processes, locks were released, no desktop schema was published,
  and the GUI did not continue to boot. This is early live-VM cancellation,
  not interruption at every individual guest installation/publication step.
- The raw base hash was unchanged after both cases. Cancellation is not a claim
  of rollback or clean guest shutdown; its disposable overlay is retained.

The first harness attempt (`desktop-preparation-live-20260924`) was rejected
before VM launch because its history directory was not mode 0700. It is retained;
the corrected harness creates private workspaces. Three unit tests additionally
cover exact process-group filtering, failed process-scan rejection, and refusing
a checksum mismatch before creating any fixture.

### Shared GUI clean shutdown

Maintenance shutdown now submits the same controller `stop` job as MCP, rather
than sending an untracked console command and interpreting log markers. Two
Tk regressions cover durable completion, serial ownership transfer, blocked
conflicting actions/closure, refusal to cancel a running shutdown, and failure
without forced power removal or loss of VM ownership.
The complete Xvfb suite passes 239 tests, including 27 Workbench tests.

The real guest GUI transfer/command acceptance passed again with this path:
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-transfer-eb5677b3029d40b5931e32264d9ed310/record.json`.
Its shutdown job `b3417a51221044518db0251a4c77d0d7` completed with `stopped: true`;
QEMU exited successfully and the stopped ext4 root had a clean primary
superblock with a verified checksum. No forced cleanup was needed. This is
not a full filesystem check or physical-device qualification. GUI boot and
normal/desktop OS shutdown integration remain separate work.

### Approved GUI host tasks

Workbench no longer directly runs host commands from `uconsole-tasks.json`.
It requires startup `--host-task-policy` plus the reviewed policy SHA-256, using
workspace ID `gui`, and submits approved task names through the same controller
path as MCP. The Tasks window displays approved invocation metadata and marks
unapproved repository names; those cannot spawn a process. The controller's
cwd lock, environment filtering, deadline, cancellation and private history apply.
This is an invocation policy, not a sandbox for mutable build code.

Tk tests verify that an unapproved task button launches no subprocess, a loaded
approval is unaffected by later policy-file edits, a synthetic non-allowlisted
environment variable is not forwarded, result callbacks execute on the UI
thread, and cancellation stops a real owned host subprocess while keeping GUI
conflict/closure guards. The complete Xvfb suite passes 237 tests, including 25
Workbench tests.

Host cancellation also has a real forked-child regression: the child ignores
TERM and outlives its launcher; the final KILL closes both inherited pipe writers.
The test rejects reaping the launcher between group signals. The launcher stays
unreaped through the ten-second grace period to pin the group ID until the last
signal. This covers same-group descendants, not deliberately detached sessions.

`tools/validate_forge_host_gui.py` also ran a harmless approved task in its exact
configured cwd, rejected an unknown task name, then cancelled a second task
after observing its live PID. The task reached terminal cancellation and the
observed process no longer existed. Private policy, job history and evidence are
retained under
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-host-1b5e976168c04f54a8ca370b12b39aee/`.
After the cancellation ownership fix, the GUI check passed again, with evidence
in `build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-host-5fa304db36ec4d7687cf3d2aec00a367/`.
No QEMU or physical device was involved. This is not installed-host-matrix,
arbitrary descendant-process, build reproducibility or release qualification.

### Supervised GUI guest commands

Guest directory listing and GUI guest tasks now share `Controller.submit_guest`
with MCP `guest_exec`, including the explicit grant, 2200-character script limit,
1–300-second deadline, cancellable supervisor, serial mutex and durable outcome.
Guest process exit status is distinct from controller-job completion; the GUI
reports a nonzero exit as a command failure. Widget callbacks run only from Tk
polling after job cleanup. GUI directory results are JSON-safe bounded pages,
not newline/tab-delimited `find` output that can corrupt filenames or exceed the
supervisor's output tail. Enumeration is live and not a consistent snapshot.

The extended `tools/validate_forge_transfer_gui.py` exercised the actual file
browser's root listing, upload/download and fixture cleanup, a one-second guest
deadline, cancellation after observing a live guest process group, and a
successful supervised command afterward. The timeout returned exit 124 with
`process_group_terminated: true`; cancellation was acknowledged and the reuse
probe verified the old process group no longer existed. GUI shutdown then
completed with zero QEMU exit, no forced cleanup and a clean checked root
superblock. Evidence and history are retained in
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-transfer-dbe3e9e965d749e2b9f84afcd9283c61/`.
The earlier transfer-only acceptance remains below as historical evidence.

Focused Tk tests cover main-thread callbacks, nonzero exits, cancellation cleanup
guards and uncertainty. Directory tests cover pagination, output bounds, symlink
classification and quoted/tabbed/multiline names. Raw serial entry, boot/display
setup and power controls remain separate integration work; this is
not a claim of complete frontend parity or installed-release qualification.

### Shared GUI/controller transfers

Workbench uploads and file-browser downloads now call the same
`Controller.submit_transfer` entrypoint as MCP. They use the GUI's actual owned
runtime and serial-operation mutex rather than launching an independent agent
process against its socket. Jobs run off the Tk thread, support cancellation,
and retain host/guest paths, checksums and outcomes in private history. GUI raw
serial operations, pause/resume, shutdown and replay reject conflicting work
until transfer cleanup is terminal. An uncertain transfer channel prevents
automatic GUI reconnection and follow-up serial commands.

`tools/validate_forge_transfer_gui.py` booted the disposable reimported image in
maintenance mode, saved editor content through Copy to guest, uploaded it,
downloaded it through another GUI/controller job and compared exact bytes. It
verified the fixture digest before removing that exact UUID-named guest file,
then used the GUI's read-only-root shutdown path. QEMU exited zero without forced
cleanup, and the stopped root superblock was clean with a valid checksum.
Evidence, transfer files and private job history are retained under
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-transfer-359d0ff094924a7fbe48d74c423cdbfe/`.

The Tk tests cover cancellation with delayed cleanup, conflicting serial/control
actions and uncertain-channel rejection. This does not qualify every failure
mode, installed-package parity or cross-client live control. Directory listing
and guest-task integration and approved host tasks were subsequently validated
in the sections above.

### Shared GUI/controller image jobs

Workbench image actions now call the same `Controller.submit_lifecycle` path as
MCP image tools. The shared path snapshots arguments, requires `image-write`,
tracks ownership and cancellation, and retains outcomes in private durable
history. GUI-selected export paths remain explicitly user-selected; MCP host
file access remains confined to its configured root. This change does not route
all other GUI operations through the controller or permit live VM adoption.

The Tk tests exercise off-UI-thread execution, conflicting-action guards,
terminal error reporting, cancellation that retains guards until cleanup ends,
and history readback from a separately scoped read-only controller. The complete
suite under Xvfb passes 226 tests, including 18 Workbench tests. Shared-entrypoint
tests also verify grants, operation filtering and argument snapshot isolation.

`tools/validate_forge_image_gui.py` ran an actual GUI boot-file refresh against
the stopped reimported image. A **separate stdio MCP process** initialized,
retrieved the completed GUI job from its private history and correctly refused
an image-write tool without a grant. Evidence is retained in
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/gui-image-4e0b5c290045410891c699f00c596d69/record.json`
alongside its private history database. The earlier `gui-image-411ba49249044d11b15211496361a4af`
record tested in-process history readback only; it is not the cross-process proof.
This validates real refresh and read-only reconnect, not every image operation,
installed-package parity, physical deployment or a full external-agent workflow.

### Firmware GPIO mailbox model

`bcm2835-firmware-gpio.patch` implements the four firmware mailbox tags used by
the production `gpio-raspberrypi-exp` driver: get/set configuration and get/set
state for logical pins 128–135. The layout/status contract follows the pinned
Linux driver's `drivers/gpio/gpio-raspberrypi-exp.c`; the surrounding message
format follows the Raspberry Pi
[mailbox property interface](https://github.com/raspberrypi/firmware/wiki/Mailbox-property-interface).
The previous binary returned zero response length and left the pin ID unchanged,
which the driver rejected. The new qtest reproduced that failure before the fix.

`make check-emulator` now includes `tools/test_emulator_firmware_gpio.py` alongside
the existing device harnesses. It exercises all eight pins, independent field
readback, logical state changes, invalid IDs/values, undersized and truncated
requests, guard words, and reset. All device harnesses and the 27 emulator
Python tests pass. The patch passes QEMU checkpatch with `--no-signoff` and has
been integrated into the pinned build helper. Migration fields are versioned,
but migration compatibility has not yet been exercised.

On the existing disposable reimported image, normal installed-module boot and
shutdown passed again in
`systemd-poweroff-67624646ebc84be98a9d361bae800b41.json`: no firmware GPIO errors
or `cleanup_srcu_struct` warning in that boot log, clean root superblock after
shutdown, fixture cleanup complete and no forced stop. A separate production
driver-binding check passed in
`firmware-gpio-a7936aa04c8e4bb583269d36999382f8.json`, confirming the platform
driver link and an eight-pin `raspberrypi-exp-gpio` chip. Both records are under
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/`.

The first binding-check attempt failed because its maintenance probe omitted
mounting sysfs; that failed record is retained as
`firmware-gpio-840ad4724e754a1c8621954c6fba7cc2.json`. The corrected validator
mounts proc/sysfs, retains diagnostic output and attempts clean cleanup even
when a probe fails. Its cleanup/error handling has four focused unit tests.

This is a logical mailbox surrogate, not electrical emulation. Polarity and
pull settings are stored/reported, not applied to external circuits. There are
no externally driven inputs, board-specific firmware initialization, dependent
rail/radio/reset effects or physical capture comparisons. All-zero model reset
defaults and malformed-request rejection are explicit model choices, not
measured firmware behavior. Do not promote these results to hardware fidelity.

### Stock Desktop-path repair

Fresh onboarding in the exported/reimported forge fixture created `forgeproof`,
but PCManFM displayed “The specified directory '/home/cpi/Desktop' is not valid”.
The user's XDG directories were correct. Both stock `/etc/skel` PCManFM templates
and their user copies contained the hard-coded `folder=/home/cpi/Desktop` line.
The installed package was `pcmanfm 1.4.0-1+rpt8`.

The explicit `tools/repair_stock_desktop.py` repair previews matches, requires
`--apply` to modify the image, preserves originals and ignores custom settings.
It is not silently installed as an emulator adapter. Removing the override
uses PCManFM's normal desktop-path fallback; see the Raspberry Pi UI
[desktop implementation](https://github.com/raspberrypi-ui/pcmanfm/blob/db37080c1820388c872d6784e675c845b791f073/src/desktop.c).
Six focused tests and the 220-test Python suite passed. In the disposable guest,
six exact stock copies were repaired (skel, the new user, and the first-boot
wizard home); after restarting PCManFM the error no longer appeared. A subsequent
typed `sudo reboot` returned automatically to the same user's desktop, with no
stale-folder error and no manual session startup. The serial login also accepted
the account created during graphical onboarding. Hardware qualification remains
unverified.

Private interactive evidence is under
`build/emulator/forge-roundtrip.lBLAbP/acceptance/reimported/desktop-record-e6b45a9d26d9440a9f9a8b0d2e54b72c/`.
The directory includes a generated password fixture and must not be published
wholesale. `screen-c01f055df5cd4a2db8d97e0c7e9afa2a.png` shows the original error;
`screen-1525dd176ba849cb9265595b1d66ce34.png` shows the repaired desktop.
QMP relative mouse input and a panel click launched LXTerminal; typed USB-keyboard
input executed `echo forge desktop keyboard proof`, visible in
`screen-49dd2bb5c6904af3949e5c5d3ddf4f6e.png`. The initial recorder process predated
its mouse-action support, so those mouse events were sent directly to its
private QMP endpoint after checking the exact `query-name` VM identity.
`screen-6a445ee5bd2249e58894168a6e33914d.png` records the post-reboot desktop.
From that running desktop, serial `sudo systemctl poweroff` completed normally:
QEMU exited zero, the recorder finished without forced cleanup, and the stopped
overlay's ext4 primary superblock was clean with a valid checksum. This is not a
full filesystem check or a desktop menu/power-key policy test. The persistent
I2C candidate was loaded from the image's normal module updates directory with
srcversion `7361393C032C6F043C21B18`; no hot replacement was used. Early boot still
logged the existing `cleanup_srcu_struct` warning; the result is not a claim of
a warning-free kernel. The recorder deliberately records observations rather
than setting an automatic desktop-pass flag. These new onboarding/repair changes
are in the reimported overlay, not in the earlier `enhanced.img` export.

### Persistent image enhancement and round trip

The disposable image round trip passes on Linux ARM64. Starting from the
checksum-verified official raw image, the shared runtime installs a uniquely
named application and enabled systemd service plus the candidate I2C module in
the guest's normal module updates directory. The test checkpoints that state,
alters the application, restores the checkpoint (retaining a safety checkpoint),
then verifies the application and installed-module selection. Normal systemd
boot/shutdown passes without hot-replacing the adapter.

Export and reimport preserve the application, enabled service and candidate
module hash; a second normal boot/shutdown also passes. Both stopped roots pass
the primary ext4 clean-state/checksum check. `config.txt`, `cmdline.txt`,
`kernel8.img` and `bcm2711-rpi-cm4.dtb` retain their original hashes. The source
image's full SHA-256 is unchanged. No forced cleanup was needed.

Evidence: `forge-roundtrip.lBLAbP/acceptance/roundtrip.json`.
Export SHA-256:
`06634fbc4778d4698d14a445c7029840b297a034d75a83eeab50c5c3c4c54d8d`.
This initial run establishes persistent service state; its result file was not
cleared before reimport boot, so fresh service execution requires the separate
restart check. That supplemental check now passes:
`forge-roundtrip.lBLAbP/acceptance/service-restart-7f7d25bd769648a9bad13afb5ce82767.json`.
It verifies the imported base/module identities, removes the old result, completes
normal installed-module boot/shutdown, and requires a new result with unchanged
application and service hashes. No forced cleanup was used. Updated full
validators clear the result before each boot.
Desktop onboarding, the IDE's full interactive loop, physical boot and full
filesystem consistency remain unqualified by this test. The evidence explicitly
records physical boot as unverified. The full Python suite passes 210 tests.

### Candidate kernel fix for late I2C power-off

Normal multi-user systemd shutdown now passes with the candidate adapter.
After PMIC unbind, ordinary adapter module replacement, and automatic PMIC
rebind, Linux reports AC online and the expected candidate module srcversion.
`systemctl poweroff --no-block` completes through systemd's shutdown targets
and `reboot: Power down`; QEMU exits zero without atomic-transfer, WARNING or
RCU messages in the shutdown segment. Before cleanup boots the image again,
the stopped overlay's primary ext4 superblock has clean-state flags and a
valid checksum. All per-run fixtures are removed without forced cleanup.
Evidence: `mcp-smoke.Ibe0TK/systemd-poweroff-eba5ed8db786473cbbd7429dd27d3049.json`.
A repeat with the final early-failure-detecting validator also passes:
`mcp-smoke.Ibe0TK/systemd-poweroff-06e2558838dd4bf1bb381107f6b0e08c.json`.
The full Python suite passes 205 tests, including fixture failure detection,
absolute deadlines, offline root checks and packaging import isolation.
This does not qualify a desktop session, a full filesystem consistency check,
permanent guest module installation, or physical hardware.

The following failed attempts are retained to explain the fixture corrections:

The first ordinary systemd acceptance attempt reached its 300-second deadline
without executing the shutdown marker. The stock image resized its root
partition, rebooted, and continued its second boot; per-run test fixtures were
removed successfully. Retained failed evidence:
`mcp-smoke.Ibe0TK/systemd-poweroff-cfaecf49e1a0456a9b0ce0dfb62958df.json`.
This is not a normal-shutdown pass or evidence of a candidate-driver failure.
The validator now records a configurable boot/shutdown deadline (600 seconds
by default) and checks the stopped overlay's primary ext4 superblock before
the cleanup boot. That check does not replace a full filesystem check.

A second run also timed out at 600 seconds, after reaching the serial login
prompt but before the shutdown marker. Cleanup again removed its fixtures:
`mcp-smoke.Ibe0TK/systemd-poweroff-3d3a32fe5146478391199583e1f28036.json`.
First-boot resizing alone therefore does not explain the remaining wait. A
separate marker-gated diagnostic timer now reports pending jobs and recent
journal entries after 120 guest seconds; it does not disable stock services.

The diagnostic run identified a test-fixture problem after multi-user startup:
the service was running, but PMIC teardown repeatedly timed out during bus
driver replacement. The pinned stock `bcm2835_i2c_remove` frees the IRQ before
`i2c_del_adapter`, while AXP child removal still performs I2C mask/ack writes.
The retained journal shows those writes returning `-110` and the test service
hitting its own 30-second startup deadline. This run was interrupted after
that observed failure, not restarted because a polling call expired. Console:
`mcp-smoke.Ibe0TK/systemd-poweroff-b3fbe79e0ee246d1be0d8dd215f9ac72-diagnostics.log`.
The first correction tried removing the PMIC module, but the traced subsequent
run established that `axp20x_i2c` is built into this image's kernel. The service
failed at that explicit step; it was interrupted for cleanup after the failure.
The validator now unbinds the exact PMIC device `22-0034` while the bus is
operational, replaces the adapter, and verifies the PMIC rebinds before checking
AC and requesting power-off. Shell tracing records any failing replacement step.
The successful corrected run is recorded above.

Extended driver acceptance now passes with a purpose-built disposable guest
test module. With preemption and interrupts disabled it calls the candidate
adapter's atomic callback for a combined register-pointer write/read, requires
AXP identity 6, checks an absent-device read returns `-EREMOTEIO`, repeats the
atomic read and then verifies ordinary IRQ-driven I2C recovery. The module is
unloaded before read-only remount and late kernel power-off, which also passes.
No forced module loading is used. Evidence:
`mcp-smoke.Ibe0TK/kernel-poweroff-4c360e6ce6cb48aaa7537d1ff9f04969.json`.
The test module builds and passes kernel checkpatch. This closes emulated atomic
read/repeated-start/NACK/recovery checks, not stalled-bus timeout, physical-bus
timing, normal desktop shutdown or full image integration.

The retained normal-shutdown trace enters `axp20x_power_off` with interrupts
disabled, then warns that `i2c-22` has no atomic transfer handler. The pinned
ClockworkPi 6.12.62 BCM2835 driver confirms the missing callback. A candidate
patch now shares its transfer state machine between ordinary IRQ-driven
transfers and bounded atomic polling, suppressing controller interrupts for
the latter. See [kernel candidate instructions](../Code/patch/cm4/20260414/README.md).
The same kernel-side contract is visible in the upstream
[BCM2835 I2C driver](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/i2c/busses/i2c-bcm2835.c)
and [AXP power-off handler](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/mfd/axp20x.c).

The candidate compiled against the existing pinned build, passes checkpatch
with sign-off checking disabled for the local candidate, and applies cleanly to
the pinned source. Module SHA-256:
`aa4b67d3365c5a394184f0586a969282b882759bce4391ff6d6e31849bf53679`.
Normal module loading succeeds without vermagic or symbol-CRC bypass; ordinary
AC reads still work. After explicit sync/read-only remount, kernel power-off
exits QEMU zero without atomic-transfer or RCU shutdown warnings. Evidence:
`mcp-smoke.Ibe0TK/kernel-poweroff-a26680586cf64af796509f39bf1fdc8b.json`.
An earlier failed validator disconnected serial too early and left a truncated
command; it is retained as failed evidence, not a kernel-path result.

These earlier results prove the late kernel path in maintenance mode; the
separate multi-user result above adds normal systemd shutdown, not desktop
shutdown. Atomic stalled-bus timeout behavior, physical CM4 operation,
full image integration and native export qualification remain open. No installed
guest module was replaced; the test loads its candidate from guest `/tmp`.

### Sampled low-capacity warnings

The AXP221 model now decodes register E6's two percentage thresholds and latches
bank-4 warning bits when a valid, present battery enters a below-threshold state
with the fuel gauge enabled. Threshold 1 is the high nibble plus 5 percent;
threshold 2 is the low nibble. Equality is not below the threshold. Register
addresses, encodings and write-one acknowledgement follow the manufacturer's
[AXP221 revision 1.6 datasheet](https://dl.linux-sunxi.org/A31/A3x_release_document/A31/PMU/AXP221%20Datasheet%20V1.6%2020131128.pdf),
sections 10.2.58, 10.2.60 and 10.2.64.

The model latches only newly entered sampled conditions. Repeated identical
samples after acknowledgement do not relatch until recovery/re-entry; disabling
the gauge or removing the battery suppresses new warnings. Re-enabling or
reinserting below threshold can latch a warning. Threshold writes reevaluate
conditions. Reset clears pending warnings without inventing a new crossing.
These re-entry semantics are an explicit model choice, not hardware-capture
qualification. Fuel-gauge integration/update timing, hysteresis and automatic
power removal remain unmodeled.

The real QEMU PMIC harness passes threshold boundaries, masked latching,
selective acknowledgement, re-entry, gauge/battery absence and reset tests.
The pinned build helper verifies the updated patch stack; the emulator gate
passes 26 tests and five device harnesses. Real Linux reads capacities
15, 14, 5, 4, 0 and 25 correctly, then passes the existing AC, battery and
KEY_POWER checks and clean shutdown. Transcript:
`mcp-smoke.Ibe0TK/power-client-128652be4949435f9d1c60f061142640.jsonl`.
Linux capacity reads do not prove warning-IRQ consumption or desktop policy;
the IRQ qualification here is the device-level harness only.

### Initial power scenario acceptance

The expanded real GUI run also qualifies running cancellation. It waits for a
completed first-event record, requests cancellation through Workbench, and waits
for the worker to terminate. The event log confirms one completed event and no
second dispatch. Both the re-enabled Live power control and Linux report AC
absent, demonstrating that cancellation did not undo the first event. GUI
power-off then succeeds with read-only root and zero QEMU exit status.
Result: `mcp-smoke.Ibe0TK/gui-client-12f97c0083174fb0b0af89144f87706a.json`.
This run includes the successful replay and initial/live-profile checks below.

Real GUI replay acceptance passes with the shared background worker: Workbench
replays AC removal/insertion, retains both verified event results, continues
serial polling, observes Linux AC online afterward, and shuts down cleanly.
Result: `mcp-smoke.Ibe0TK/gui-client-1b63e4aa38174e17a19eb1d0e2b63952.json`.
All 190 Python tests pass. The Tk worker test checks off-main-thread execution,
snapshot handling, conflicting-control and close guards, cancellation and
main-thread terminal cleanup. Real running cancellation is qualified through
MCP below; this GUI run qualifies successful replay, not GUI cancellation.

Expanded real replay acceptance now checks the two AC-driver child IRQ counters
before and after the removal/insertion cycle: both increase, not merely their
shared parent GPIO/PMIC interrupt. A second schedule is cancelled only after its
first event has a completed readback record. The job terminates as cancelled,
the log contains no second dispatch, and both model query and Linux report AC
absent. Subsequent jobs, clean shutdown and read-only history reconnect pass.
Transcript: `mcp-smoke.Ibe0TK/mcp-client-cf3f61c7e1544bd887808a30e26e37c0.jsonl`.
All 189 Python tests pass, including rejection of parent-only IRQ evidence.
This proves driver IRQ delivery and retained partial effects for this schedule,
not userspace notification timing or arbitrary device-event fidelity.

Host-clock MCP replay passes against QEMU and Linux: an AC removal/insertion
schedule records two verified events, with insertion completing no earlier than
its 1000 ms deadline, and Linux subsequently reports AC online. Clean shutdown
and read-only history reconnect pass. Transcript:
`mcp-smoke.Ibe0TK/mcp-client-bf0ddacb5a5544d7828a6b3a563e18c1.jsonl`.
The full 187-test suite passes; an additional controller cancellation test
passes in the focused MCP suite, checking that cancellation after one event
retains partial evidence and releases workspace ownership. Replay unit tests
cover bounds, ordering, snapshots, cancellation, paused-VM rejection and failed
readback. This does not prove Linux observed every short-lived transition,
virtual-time determinism or physical timing.

Workbench live-field acceptance also passes. Its real Tk controls read AC
absent, apply AC present with the shared model interface, and observe Linux
`axp22x-ac/online=1` through the GUI serial path before clean shutdown. Each
query/change leaves a `power-event-*.jsonl` record with runtime identity and
outcome. Retained acceptance result:
`mcp-smoke.Ibe0TK/gui-client-19570af3d755468687bc8de94a29075a.json`.
All 182 Python tests pass. GUI tests additionally cover missing ownership,
invalid values before mutation and retained success/failure evidence. This does
not qualify every field's guest effect or desktop power-key shutdown policy.

Live MCP power controls now pass real-guest acceptance too. After booting the
battery-discharge profile, the client queries AC absent, sets AC present,
verifies before/after model samples, reads Linux `axp22x-ac/online` as 1, and
queries the updated state again. Clean shutdown and read-only history reconnect
also pass, retaining query and mutation job results. Transcript:
`mcp-smoke.Ibe0TK/mcp-client-4028e503e7384710af9c6a26cfce16c3.jsonl`.
All 181 Python tests pass, including grant enforcement, exactly-one-field
validation, quantized readback and silent-no-op rejection. This live test covers
AC insertion, not every runtime field, desktop shutdown policy or timed replay.

The managed runtime validates schema-1 JSON before refreshing boot artifacts or
launching QEMU. It starts the VM paused, checks all required QOM property types,
applies the profile and verifies each readback before resuming. Evidence in
`scenario-<runtime-identity>.json` records the input SHA-256 and requested versus
observed values, including voltage quantization. Failed application terminates
only the owned child and releases its workspace lock.

The real guest passes with `docs/scenarios/battery-discharge.json`: AC absent,
battery present, 3300000 microvolts, -250000 microamps and 25 percent capacity.
The same run verifies subsequent AC interrupts, charging/discharging states,
KEY_POWER down/up and clean maintenance shutdown. Transcript:
`mcp-smoke.Ibe0TK/power-client-0fea8b1d99364a71914f2bfb2a22ba36.jsonl`.
All 172 Python tests pass, including eight scenario tests covering malformed
input, immutable snapshots, model support, readback failure and startup cleanup.
This does not qualify timed replay, battery chemistry, physical timing or native
hardware boot.

Workbench now offers session-local profile load/clear controls. Tk tests cover
snapshot loading, invalid-file and cancelled-dialog preservation, busy-state
rejection, shared-runtime handoff and display of verified evidence. These GUI
tests use a mocked runtime. The full Python suite passes 177 tests.

The separate real Tk acceptance run now also passes: Workbench loads the example
profile, starts QEMU, submits the probe through its serial-entry widget, observes
Linux AC/battery readings and performs its own Power off sequence. The root
filesystem reports read-only before QEMU exits with status zero. Only the native
file chooser is automated; runtime launch, serial polling and shutdown are real.
The retained result includes model evidence and console output:
`mcp-smoke.Ibe0TK/gui-client-e224c52ed40544dfae7cf32dca090c23.json`.
This tests the maintenance GUI, not fresh desktop onboarding or native hardware.
Repeat with a disposable, prepared workspace:

```sh
xvfb-run -a /usr/bin/python3 tools/validate_forge_gui.py --workspace /path/to/disposable-workspace
```

On a host with a display, omit `xvfb-run -a`. Failure cleanup may force power
removal from the validator's owned guest; never use a valuable running workspace.

MCP acceptance also passes with a files-root-bounded startup profile. The real
stdio client verifies the boot result's source digest and requested/readback
state, checks Linux AC and battery readings, stops cleanly, then reconnects
read-only and verifies durable results without adopting a VM or gaining
cancellation authority. Transcript:
`mcp-smoke.Ibe0TK/mcp-client-27b2a1167b7d4d5da7639d21967b90af.jsonl`.
All 174 Python tests pass, including MCP permission/path boundaries and a
profile changed after submission that cannot alter the queued snapshot. The
first live MCP attempt failed because the validator used `axp20x-ac` instead of
the driver's `axp22x-ac` sysfs path; that partial transcript is not a pass.

### Power-key press/release through the Linux input driver

The PMIC's new QMP/QOM `power-key-pressed` boolean represents a debounced
active-low input. Changes latch the falling/rising edge interrupt bits in bank
5; unchanged assignments generate no new edge. Masked edges remain pending,
acknowledgement is selective, and reset preserves a held key without inventing
another press. The added key state uses migration version 2, with an unpressed
default for version-1 input; real migration remains unqualified.

The PMIC qtest checks masking, acknowledgement, repeated state and held-key reset.
The guest validator opens the `axp20x-pek` evdev device before injection, waits
for its actual key-down event before releasing, and requires exactly
`EV_KEY/KEY_POWER` values `[1, 0]`. This follows the existing Linux
[power-key driver](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/input/misc/axp20x-pek.c)
without substituting a generic QEMU keyboard key. The final rebuilt QEMU passes
that test, the AC/battery checks, and clean maintenance shutdown. Transcript:
`mcp-smoke.Ibe0TK/power-client-46f13d73df0a439bbc3e3e3b7d87a02f.jsonl`.
All 26 emulator tests and five device harnesses pass.

This qualifies press/release delivery only. Debounce duration, short/long-press
classification, startup/shutdown hold timers, forced power removal and guest
desktop power-button policy remain unfinished. The maintenance test deliberately
has no desktop/session manager that could turn a key event into shutdown.

### Battery current, capacity and charge state

QMP/QOM now exposes signed `battery-current-ma` (-4095 to 4095, positive for
charging) and `battery-capacity` (0–100 percent). Present batteries have a valid
fuel-gauge reading. Current samples are mutually exclusive between the charge
and discharge ADC registers; guest writes cannot change samples or capacity.
Nonzero current requires a present battery. Positive current additionally
requires AC, the guest charger-enable bit, and capacity below 100. Invalid
updates preserve the previous value. AC loss, battery removal and guest charger
disable stop charging. Reaching capacity 100 while charging stops current and
latches charge-complete; entering charging latches charge-start. Reset preserves
the injected samples while clearing events. These are controlled samples and
state transitions, not autonomous chemistry or a charge-current regulator.

The expanded PMIC qtest checks range/condition rejection, both interrupt events,
acknowledgement, repeat-state idempotence, reset, removal and guest charger
disable. Real guest validation observes `Charging/500000/50`, `Full/0/100`,
`Discharging/-250000/25`, and `Not charging/0/25` through the battery driver's
`status/current_now/capacity` files (current is in microamps). It repeats the AC,
presence and voltage checks and clean maintenance shutdown. Transcript:
`mcp-smoke.Ibe0TK/power-client-a70ad3e3ed244918a60376233fa83bad.jsonl`.
All 26 emulator tests and five device harnesses pass. Current/capacity sampling
does not yet integrate over time; low-battery threshold IRQs, thermal behavior,
ADC enable/rate timing and charging voltage/current limit enforcement remain.

### Battery presence and sampled voltage through guest drivers

The PMIC exposes QMP/QOM `battery-present` and `battery-voltage-uv` properties.
Insertion/removal changes status bit 5 and latches the corresponding bank-2
interrupt independently of its mask. Presence and injected voltage survive
reset without generating new insertion events. Voltage is a sampled 12-bit
value at registers 0x78/0x79: accepted values are 0–4504500 microvolts, rounded
down to 1100-microvolt steps. Guest writes cannot alter those sample registers;
invalid QMP values leave the last sample unchanged.

The generated emulator device tree now includes the PMIC ADC child so the
unmodified Linux IIO and battery drivers bind. The expanded real-guest validator
loads those drivers, checks absent/present/absent transitions, and observes
3300000 and 4400000 microvolts through battery `voltage_now`. It also repeats
AC-driver interrupt acceptance and clean maintenance shutdown. Final transcript:
`mcp-smoke.Ibe0TK/power-client-63ac0627c7444e03843db56abfbe4b83.jsonl`.
The updated PMIC qtest covers event masks/acknowledgement, unchanged-state
idempotence, voltage bounds/quantization, read-only samples and reset persistence.
All 26 emulator tests and five device harnesses pass on the final build.

Register interpretation follows Linux's
[AXP battery driver](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/power/supply/axp20x_battery.c)
and [AXP ADC driver](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/iio/adc/axp20x_adc.c).
This is sampled-value injection, not yet an analog source/conversion model:
ADC-enable/rate timing, absent-battery voltage behavior, charge/discharge current,
capacity, thermal faults and low-battery thresholds remain unfinished. It does
not validate the separate carrier ADC101C or physical battery behavior.

### Guest AC-driver acceptance through PMIC, GPIO and GIC

The carrier patch now connects the PMIC's logical IRQ through inversion to
GPIO2 and adds the corresponding level-low device-tree interrupt binding.
The CM4 model also routes the shared I2C completion interrupt to GIC SPI117;
without it, the real PMIC driver timed out while initializing its IRQ masks.
The new `bcm2835-i2c-status-irq.patch` updates the I2C IRQ output when DONE is
acknowledged. A qtest first reproduced the stale output, then verified its
deassertion after the fix. The PMIC qtest covers the complete active-low
PMIC-to-GPIO-to-GIC path, including a held level that cannot be acknowledged away.

`validate_emulator_power.py --workspace build/emulator/mcp-smoke.Ibe0TK`
passes against the final rebuilt QEMU and the disposable official-image guest.
The guest's unmodified AC power driver reports `online=present=1`, then zero
after removal, then one after insertion. Both events increment the AC driver's
own interrupt counts, not merely its parent GPIO/PMIC counters. The maintenance
guest shuts down through the clean read-only-remount path. Final transcript:
`mcp-smoke.Ibe0TK/power-client-557987a93ee347108f30fc25cfc42742.jsonl`.
An earlier passing run preceded the I2C acknowledgement fix; the earlier failed
run identified the missing I2C GIC route and does not count as acceptance.

The final `make check-emulator` run passes 26 tests and five device harnesses.
This qualifies the AC status/interrupt subset, not desktop battery UI, userspace
uevent consumption, suspend/wake, power-key behavior or physical timing.
Guest boot still logs missing regulator/ADC/USB-power child configuration and
unimplemented firmware-GPIO warnings; these have not been hidden or waived.

### BCM2711 digital GPIO events and GIC delivery

The new `bcm2838-gpio-interrupts.patch` extends the BCM2838 GPIO model actually
used by `raspi4b` (not QEMU's separate BCM2835 model). It adds 58 input lines,
separate output latches, rising/falling and high/low detectors, write-one-to-clear
event status, and four group outputs connected to GIC SPIs 113–116. Reset clears
detectors/events but preserves external input levels. New migration fields are
versioned; real migration remains unqualified.

`tools/test_emulator_gpio.py` passes against the rebuilt QEMU. It injects edges
at pins 0, 2, 27, 28, 31, 32, 45, 46, 53, 54 and 57, checks synchronous and
asynchronous detector registers, verifies asserted levels cannot be acknowledged
away, and observes both GPIO IRQ outputs and GIC pending bits. It also checks
reserved bits, output latches versus input levels, upper-bank outputs and reset.
The first harness attempt used an incorrect qtest GPIO name and aborted its
diskless QEMU; using the documented `unnamed-gpio-in` name fixed the harness.
The expanded `make check-emulator` gate now includes five device harnesses.

Register semantics follow the
[BCM2711 peripheral manual](https://datasheets.raspberrypi.com/bcm2711/bcm2711-peripherals.pdf),
sections 5 and 6. This is a digital functional model: synchronous and asynchronous
edges are handled identically, with no clock sampling, glitch filtering or
electrical pull-resistor simulation. The later combined-driver acceptance above
adds PMIC pin wiring and guest device-tree interrupt binding.

### AXP221 AC state and interrupt controller foundation

The expanded `axp2xx-poweroff.patch` adds AXP221-only behavioral controls to the
pinned QEMU model. The `ac-present` boolean is readable/writable through QMP
`qom-get`/`qom-set`; `irq-active` is read-only. Discover the `child<axp221_pmu>`
under `/machine/unattached` rather than assuming an anonymous device number.
AC changes update the input status register and latch insertion/removal events.
Five interrupt banks implement enable masks and write-one-to-clear status.
Input/operation status and chip ID reject guest writes. Reset preserves the
external AC state, restores masks, clears pending events and updates the IRQ
output. Migration reconstructs that output from the existing register state;
migration has not yet been qualified by a real migration test.

The model follows the AC status bits in the Linux
[AC power driver](https://raw.githubusercontent.com/torvalds/linux/master/drivers/power/supply/axp20x_ac_power.c)
and the insertion/removal mapping and acknowledgement contract in its
[MFD driver](https://raw.githubusercontent.com/torvalds/linux/master/drivers/mfd/axp20x.c).
This AC control currently represents a valid connected supply or an absent
supply, not overvoltage, brownout, or simultaneous VBUS behavior.

The rebuilt QEMU passes the expanded PMIC qtest: real I2C reads/writes and QMP
state changes agree; masked events remain pending; unmasking raises the actual
intercepted IRQ output; selective acknowledgement lowers it; repeated identical
AC state does not generate another event; reset and read-only registers behave
as described. Existing NACK, bounds and poweroff checks still pass. The normal
build helper verifies the combined patch on the modified source tree. The
complete `make check-emulator` gate passes all 26 tests and four device harnesses.

The PMIC output is a logical assertion, inverted at the carrier GPIO pin by the
later integration above. The BCM2838 GPIO implementation supplies the controller
foundation and the guest AC driver now handles insertion/removal interrupts. Battery,
ADC101C, rail-dependent peripherals, power-key events and the versioned scenario
framework remain unfinished. No retained diagnostic guest was restarted for
these diskless checks, and register-level shutdown does not fix Linux's late
atomic-I2C shutdown problem.

### Canonical workspace exclusion and independent-process ownership

Controller job exclusion now uses the canonical workspace directory rather
than its registration name. A symlink-alias regression verifies that two
registrations cannot submit simultaneous jobs and that the alias becomes
usable after the first job finishes. A separate Python process holds the real
filesystem lock while an MCP controller attempts boot: the boot fails busy,
does not refresh artifacts or launch QEMU, preserves the prior serial log,
and leaves the other process alive. After that process releases the lock,
the workspace is lockable again. All 19 MCP tests pass. This proves exclusion,
not attachment to or scheduling work through a different controller's VM.

The refreshed `make check-emulator` gate passes 26 emulator tests and the four
real QEMU watchdog, AXP221, GIC debugger-access and USB remote-wakeup harnesses.
Register-level poweroff success still does not qualify Linux PMIC shutdown.

The expanded suite exposed a reproducible Tk teardown failure: after GUI tests
destroyed their windows, cyclic Tk references survived until an executor worker
triggered collection (`Tcl_AsyncDelete: async handler deleted by the wrong
thread`). GUI fixtures now release their application/root references and run
cyclic collection on the creating thread. All 164 tests pass under Xvfb after
this change, without skips. The earlier aborted runs are not passing evidence.

### In-flight MCP download cancellation

`validate_forge_mcp.py --cancel-download` booted the disposable maintenance
guest, created a 4 MiB regular file, and observed its actual base64 payload
on the serial console before requesting cancellation. The job reached
`cancelled` after the serial transaction finished, without publishing the host
destination. A subsequent guest command and clean maintenance shutdown passed.
Read-only reconnect recovered the same durable outcomes and rejected historical
cancellation. Transcript:
`mcp-smoke.Ibe0TK/mcp-client-cfeab57afd4e4230bf396f9b67179ac0.jsonl`.
This verifies cooperative cancellation before publication, not immediate
interruption of serial I/O or rollback of an already published file.

### Digest-pinned host tasks and final history context

The owner-approved host-task policy has nine tests covering digest validation,
fixed arguments, environment filtering, workspace binding, immutable policy
snapshots, directory locking, cancellation and deadlines. Approval metadata
is persisted before execution and retained for failed or cancelled jobs.
This is an invocation policy, not a sandbox for approved mutable project code.

Real stdio validation executed a harmless pinned fixture, rejected argument
overrides and unknown tasks, kept the original snapshot after policy-file edits,
and recovered outcomes and approval context on a read-only reconnect without
adopting a VM. Transcript:
`mcp-smoke.Ibe0TK/mcp-client-62b7885f06204c27831cb16ec241b344.jsonl`.
Its final record reports `validation: passed`. The current full repository suite
passes all 162 tests with system Python under Xvfb, with no skipped GUI tests.
These are local Linux ARM64 checks, not cross-platform or hardware qualification.

### 2026-09-24 durable MCP job history

The MCP server now records accepted jobs and bounded terminal outcomes in a
private SQLite store with full synchronous commits. A queued-record failure
prevents execution; a terminal-record failure reports failure with a warning to
inspect effects rather than repeat them. Terminal publication waits for the
history commit. Reconnected sessions can page through summaries and read
outcomes only for their registered canonical workspace paths. Persisted
queued/running records are `unresolved`, not proof of live work. History grants
neither cancellation authority over old jobs nor VM ownership.

Nine history tests cover reconnects with renamed workspace registrations,
scope isolation, private storage/link rejection, unrelated-database preservation,
write failures before and after effects, queued cancellation, commit ordering,
concurrent independent connections and pagination. All 153 repository tests
pass with system Python under Xvfb.

Real validation passed the normal boot/execute/transfer/capture/clean-stop loop,
then restarted the MCP server without any grants and recovered the same job
outcomes. Inspection showed no owned runtime and historical cancellation was
rejected. Transcript: `mcp-smoke.Ibe0TK/mcp-client-f9196924c2c64179aef29ced2618bd89.jsonl`.
The test's `.mcp-history/` directory and database were verified as modes 0700 and
0600 respectively. This is local Linux history/reconnect evidence, not
cross-client VM attachment, guest crash recovery, or Windows support; the
history implementation currently requires POSIX permissions.

### 2026-09-24 controller job handoff ordering

A deterministic test delayed future done callbacks and reproduced a terminal
job whose workspace still rejected a subsequent operation as busy. Cleanup now
releases ownership in the worker's `finally` block before the executor publishes
the terminal future. Queued cancellation, which does not run the worker, still
releases through its done callback. Ownership is keyed by job ID so stale or
duplicate cleanup cannot clear a newer job's ownership. Tests cover terminal
handoff with delayed callbacks, stale cleanup while a replacement runs, and
queued cancellation without executing work. The 17-test MCP and six-test
transfer suites pass, as do all 144 tests in the full system-Python/Xvfb suite.
This is in-process controller concurrency evidence, not
cross-client VM adoption or distributed scheduling qualification.

### 2026-09-24 cooperative transfer cancellation

MCP uploads and downloads now accept running cancellation at acknowledged
transaction boundaries. Tests verify an upload stops between chunks without
installing its destination, cancellation after an acknowledged download avoids
host-file creation, and an upload that has already committed reports completion
despite a late cancellation request. Staging cleanup ignores the cancellation
flag, but an uncertain cleanup propagates to runtime quarantine rather than
being mislabeled as successful cancellation. Controller tests verify both
transfer operations report terminal cancellation without stopping the VM.
Guest-command preparation also uses these checks while uploading its temporary
supervisor and script, before any job is launched.

The six targeted transfer tests pass; the full system-Python/Xvfb suite passes
all 141 tests with no skips. Real validation was initially deferred while host
load was near 139 with about 3 GiB available memory and exhausted swap. The
previous deadline attempt was confirmed terminal, not restarted merely because
observation had timed out.

After host capacity recovered to about 22 GiB available memory and current load
near 11, real `--cancel-upload` validation passed in `mcp-smoke.Ibe0TK`. The
client observed four new acknowledged transactions before requesting cancellation
of an 8 MiB upload. The job reported cancelled; a subsequent download verified
the original destination still contained `preserved`, and before/after listings
confirmed no extra `uconsole-agent-*` staging file remained. Another command and
clean maintenance stop succeeded. Transcript:
`mcp-client-c1e25269eadf4612aac225ec1e269040.jsonl`. This is real upload-cancellation
evidence; cancellation of an in-flight download remains separately unqualified.

### 2026-09-24 supervised maintenance guest commands

MCP `guest_exec` now uses a temporary guest-local Python supervisor and short
serial control transactions. Each script has its own process group, bounded
16 KiB stdout/stderr tails, and a guest deadline. Cancellation/deadline cleanup
sends TERM, escalates to KILL, and confirms no live group members before
acknowledging termination. The leader remains unreaped during cleanup to avoid
PID/PGID reuse. Ordinary shell completion also cleans up background group
members; independently daemonized services and committed effects are not
rolled back. No persistent service or network listener is installed.

The real MCP cancellation scenario passed in `mcp-smoke.Ibe0TK`: inspection
observed guest process group 233, cancellation terminated the TERM-ignoring job,
another guest command succeeded, and maintenance shutdown completed cleanly.
Transcript: `mcp-client-2dadbb6e58a4447baa1e3e701ef2c699.jsonl`. An earlier
ordinary execution/transfer/capture/stop scenario passed with the supervisor
in `mcp-client-4e218e11ae644f5d9c0b8c66f76677ac.jsonl`.

An initial cancellation attempt exposed kernel console text (`crng init done`)
inside an otherwise acknowledged polling response. Explicit state/result
framing and a regression fixture now prevent interpreting that noise as job
data. Process-group startup publication is atomic as well. All 135 repository
tests pass under system Python and Xvfb, including real Linux subprocess tests
for TERM-ignoring cancellation/deadlines, bounded output and background cleanup.

The first separate real guest-deadline attempt was unqualified: boot/client
communication timed out before the command stage while host load exceeded 260,
available memory was about 4 GiB and swap was exhausted. That attempt's
transcript `mcp-client-f050efbcee3b43cda8fbb7ce07fc7c96.jsonl` is partial, not a
pass; its MCP and QEMU processes were subsequently confirmed absent. No extra
VM was launched under that pressure. After capacity recovered, the real deadline
scenario passed: a TERM-ignoring group returned exit 124, `timed_out: true` and
confirmed process-group termination; another command and clean maintenance stop
then succeeded. Transcript: `mcp-client-a3d86dd5d2b8447fb9d95f202d7511f7.jsonl`.
Desktop guest execution and the other milestone gates remain open.

### 2026-09-24 guest command timeout quarantine

The serial transport now distinguishes an unacknowledged dispatched command
from preflight errors and acknowledged nonzero exits. Timeouts, EOF, partial
write errors, output-limit failures and host interrupts after dispatch report
`GuestChannelUncertain`. Runtime inspection exposes this condition and further
commands, transfers and clean stop are rejected while VM ownership is retained.
Uploads skip serial cleanup after an uncertain chunk; uncertain cleanup after
installation is reported rather than hidden. No guest termination or rollback
is inferred from a host wait timeout.

The real MCP client `--timeout-guest` passed against disposable workspace
`mcp-smoke.Ibe0TK`. A `sleep 30` command with a one-second host timeout failed
with unknown completion; subsequent execution and clean stop were rejected,
inspection reported an uncertain channel and an owned running guest, and an
explicit forced stop released the VM and workspace lock. Transcript
`mcp-client-58920917f03f4ed8ace1b7c629ec94b7.jsonl` ends in a successful validation
record. This deliberately forced-stop test may leave the test filesystem
unclean; it is not guest cancellation, filesystem recovery, rollback or
clean-shutdown qualification. This test predates the supervised MCP command
path above; quarantine remains the policy for unacknowledged short control
transactions and failed supervision. Authenticated desktop guest execution
remains unfinished work.
All 127 repository tests pass with the system Python under Xvfb, with no skips.

### 2026-09-24 isolated USB remote-wakeup interrupt regression

An ordinary schema-13 desktop boot in `schema13-recovered` stalled without a
SysRq request. The watchdog observer paused it with 2.83 seconds remaining.
The paused VM and its disk were retained, not resumed or reset for this test.
Symbols extracted from that exact `kernel8.img` using
[vmlinux-to-elf 1.2.2](https://github.com/marin-m/vmlinux-to-elf) identify CPU0
inside `dwc2_handle_hcd_intr`, CPU1 waiting for a spinlock through
`bcm2835_mmc_timeout_timer`, and CPUs2/3 in normal `cpu_do_idle`. Matching idle
PCs and masked interrupts alone were not evidence of a four-CPU deadlock.
The extracted ELF is `schema13-recovered/kernel-symbols.elf`; its text base
is `ffffffc080000000`, matching the captured instruction addresses.

Read-only register evidence: GINTSTS `05000029`, GINTMSK `f3000806`, HPRT0
`000210c5`; GIC active SPI105 is DWC2. The enabled interrupt is PRTINT despite
no connection, enable or overcurrent change bit in HPRT0. QEMU's remote-wakeup
callback raises PRTINT after setting RES, whereas the
[Linux port handler](https://github.com/torvalds/linux/blob/v6.12/drivers/usb/dwc2/hcd_intr.c)
only acknowledges the three actual port-change conditions. The
[Linux wakeup handler](https://github.com/torvalds/linux/blob/v6.12/drivers/usb/dwc2/core_intr.c)
instead handles WKUPINT and schedules completion of resume signaling.

`tools/test_emulator_usb_wakeup.py` independently reproduces this model error
without a Linux image: direct keyboard on port 1, endpoint-zero USB DMA
SET_FEATURE(remote wakeup), suspend, then QMP key-down. Explicit port selection
avoids QEMU's automatic hub and ensures the control transfer targets the
keyboard. The original model produces PRTINT with RES set and no change bits.
`dwc2-remote-wakeup.patch` raises WKUPINT and allows software to finish RES,
clearing SUSP at resume completion or reset. The regression now passes wakeup,
acknowledgement, resume completion and awake key-release checks.

The pinned emulator rebuilt successfully; `make check-emulator` passes all
four device harnesses and its 21 Python tests. The full suite passes 114 tests
under Xvfb. This is a reproduced and corrected model contract error, not yet
a fresh Linux desktop stability or physical-hardware qualification result.
The existing paused VM still runs the old binary mapping; rebuilding the
executable does not update that running process.

### 2026-09-24 rebuilt-emulator desktop comparison

Prepared `build/emulator/usb-wakeup-fixed` independently from the recovered raw
image, SHA-256 `96a15c09bb49448031f9dd25230e58265cd9711eecb05910444cffea69612a10`,
and installed schema 13 with the normal `configure-display` command. This is
a fresh workspace using the recovery image, **not** fresh official-image
onboarding qualification. The original paused failure VM remains untouched.

The rebuilt emulator booted the ordinary desktop profile (root initially ro,
no SysRq override or diagnostic root console). The desktop took about three
minutes to appear. With HPRT0 `00021085` confirming a suspended root port,
Ctrl-Alt-T opened the terminal; typed `id`, `uname -r`, `uptime` and
`sudo -n true` completed as `forgeproof`, UID1000, kernel `6.12.62-v8+`.
QMP relative mouse motion and a left click opened the desktop application
menu. Two bounded watchdog observations (60 and 180 seconds) completed with
ongoing refreshes, no observer pause and no watchdog reset. Screenshots in the
workspace: `desktop-late.png`, `terminal.png`, `command-proof.png`,
`mouse-proof.png`.

After closing the menu, an initially too-fast automated typing attempt lost
the leading `su` and produced the harmless shell syntax error `do reboot`.
The subsequent paced `sudo reboot` completed normal systemd service shutdown
and filesystem unmount targets, then logged `Restarting system` at guest
299 seconds and began a second kernel boot. Post-reboot desktop and final
poweroff/filesystem checks were then performed as follows; successful reboot
initiation alone was not treated as closing those gates.

The second desktop returned and opened a terminal (`reboot-command.png`).
It displayed a PolicyKit duplicate-agent dialog (`reboot-ready.png`):
`pgrep -af polkit; loginctl` showed `lxpolkit` and
`polkit-mate-authentication-agent-1`, with one actual user session and its
systemd user-manager session (`polkit-state.png`). Offline inspection confirms
`rpd-x/desktop.conf` selects `polkit/command=lxpolkit`, while the MATE agent's
XDG autostart excludes only GNOME/KDE. This startup conflict remains open;
the test did not establish a duplicate LightDM user session.

Typed `sudo poweroff` completed service shutdown and unmount targets; QEMU
exited with status 0 without a host quit/kill. The kernel still emitted the
previously observed missing atomic-I2C handler and RCU-context warnings in
the AXP poweroff path. This successful exit does not resolve those warnings
or establish repeatable fault-free PMIC shutdown.

Normal export produced `usb-wakeup-fixed/shutdown-verified.img`, SHA-256
`9fd113bc5117c17d85cfcb24ffbb04d42f8000cca59585a0264d0f2eff95a18e`, passing the
export superblock guard. `sfdisk --json` confirmed partition 2 starts at
1056768 sectors and spans 15720448 sectors (512-byte sectors). That partition
was exposed through a **read-only** loop device; `e2fsck -f -n` completed all
five passes with exit 0. The loop was detached afterward. No repair was
performed on this export. This verifies the filesystem after this particular
boot/reboot/poweroff cycle, not physical boot or all application contents.

### 2026-09-24 scoped PolicyKit adapter hardening

The duplicate-agent fix is now schema 16: a conditional Xsession hook keeps
the original `rpd-x` session identity, checks its selected PolicyKit command,
and uses an XDG autostart override only when `lxpolkit` is selected and present.
It does not edit system or personal autostart files. Tests execute the generated
Python helper and shell hook, covering the exact boot marker, unchanged native
environment, other sessions, alternative user-selected agents, missing agent
binary, inherited settings, malformed configuration and unchanged native files.
The helper writes only a private runtime profile, forwarding existing system
entries and overriding the competing autostart entry. Tests also check profile
permissions, forwarding and byte-exact reconstruction of chunked setup files.
The full Xvfb suite passes 116 tests; `make check-emulator` passes 23 Python
tests and all four device harnesses.

An intermediate schema-14 experiment used a new desktop session name. It
eliminated the competing MATE agent (`usb-wakeup-fixed/schema14-polkit-result.png`)
but changed desktop defaults and lost the normal terminal shortcut. It was
replaced in source, not accepted as completion. Source inspection also found
that [LightDM greeter logins persist session choices](https://github.com/canonical/lightdm/blob/main/src/seat.c),
so introducing an emulator-only session identity is undesirable for the shared
image. Schema 16 does not install that experimental desktop entry. Full greeter
and native-session preference preservation still needs qualification; the
current live proof uses autologin. The subsequent schema-15 live trial retained
the original name but used a sparse XDG directory. Its 240-second watchdog
observation completed without a pause or reset.

Schema 16 was then hot-installed into `schema15-polkit`; every generated file
was checked against the source SHA-256 before and after testing. Following a
LightDM restart, the session retained `rpd-x`, ran one `lxpolkit` and no MATE
agent, and Ctrl+Alt+T launched `x-terminal-emulator`. The visible terminal kept
the PiXtrix decorations (`schema16-restart-shortcut.png`). This final restart
test did not use Openbox's manual reconfigure operation. Terminal presentation
was delayed; checking only for a process named `lxterminal` also missed the
actual `x-terminal-emulator` process. Neither should be reported as a failed
shortcut without checking the rendered result.

An earlier sparse-profile trial had written a personal PCManFM default profile
with a black background and Wastebasket. Read-only inspection confirmed that
file was absent from the checksummed schema-13 export. Disabling the helper
temporarily and restarting LightDM retained those saved preferences; restoring
the forwarding helper does not reset them. No personal preferences were erased.
An Openbox reconfigure trace separately confirmed successful reads through the
forwarded system `openbox/rpd-rc.xml` and the PiXtrix theme. The absent personal
`openbox/rpd-rc.xml` also predates this adapter; its absence alone is not proof
that Openbox failed to load its system configuration.

Hot installation used a temporary diagnostic root shell on this owned VM's
private serial socket, bootstrapped through its graphical terminal and existing
NOPASSWD sudo. It is not the shipped desktop-mode MCP execution transport.
The transient shell was stopped afterward, and the serial log records
`FORGE_DEBUG_CONSOLE_REMOVED` followed by the ordinary `clockworkpi login:`
prompt. The original helper was restored and byte-verified; no diagnostic
service was installed persistently. Workspace metadata still records schema 15:
this hot-update check does not replace normal setup/migration qualification or
fresh-overlay onboarding with schema 16.

The intermediate guest reached `Power down` but did not exit QEMU after the
atomic-I2C/RCU warnings. Its state was explicitly paused and retained in
`usb-wakeup-fixed`; the previous successful cycle transcript was preserved as
`schema13-cycle-serial.log`. Thus the earlier successful poweroff must not be
generalized to reliable PMIC shutdown. The schema-15 test uses a separate
workspace `schema15-polkit`, imported from the checksummed, filesystem-verified
`shutdown-verified.img` above. The hot-update desktop check above passed, but a
fresh final-schema boot/reboot/export cycle remains pending.

### 2026-09-24 forge adapter hardening

Fresh workspace `build/emulator/forge-schema7` was prepared from the raw image
hash below. `configure-display --timeout 120` booted maintenance mode, installed
schema 7 through private Unix sockets, remounted root read-only and exited
successfully. This verifies installation, not desktop login or physical boot.
Per-attempt serial/QEMU logs are retained in that workspace.

`make check` passed 41 tests with 11 display-dependent tests skipped and passed
ShellCheck. All 11 skipped GUI tests then passed under Xvfb. New regression
checks cover conditional desktop activation, isolated Xorg configuration,
bounded serial transactions, private setup endpoints, failure cleanup and
preservation of existing serial logs/configuration on setup failure.

### 2026-09-24 checkpoint lifecycle

The full local suite passed 62 tests under Xvfb, including real qcow2 conversion,
standalone-checkpoint inspection, byte-for-byte restore, inherited lock lifetime,
GUI launch exclusion and simulated interruption during multi-file restore.
The CM4 workspace `build/emulator/forge-schema7` was checkpointed as
`configured-display`, then restored through the CLI. Its safety checkpoint is
`before-restore-460f72f803b5432a80633ddb04e9b426`. Both retain manifests and disk
images. This establishes lifecycle operation on the real image, not post-restore
desktop login or physical-hardware compatibility.

### 2026-09-24 boot artifact refresh

Refresh completed against `forge-schema7` after its checkpoint restore, reading
the current qcow2 guest boot partition. The extracted kernel and regenerated
DTB hashes match the image identities below. The new real-qcow2 regression test
then changed kernel bytes in an overlay and verified refresh changes the
extracted kernel hash without changing the backing image. Tests cover compressed
kernel corruption, unsupported configuration directives, preservation on failed
preflight, and rejection of launch rather than fallback to stale files.

A refreshed desktop boot reached systemd services, waited on
`systemd-networkd-wait-online.service` with no network adapter selected, then
advanced to first-boot partition resizing at guest time 198 seconds. The
captured screenshot shows the earlier boot framebuffer, not a usable desktop.
Desktop-profile acceptance remains open; this is not a refresh pass for
the complete GUI workflow. All 68 local tests passed under Xvfb. Evidence is in `forge-schema7/serial.log` and
`forge-schema7/desktop-refresh.png`.

### 2026-09-24 owned runtime and desktop session finding

Workbench now uses `forge_runtime.Runtime` with private sockets and a per-launch
QEMU identity. Unit tests cover wrong-identity rejection, dead-child rejection,
workspace exclusion, serialized guest operations and failed-remount behavior.
`tools/validate_forge_runtime.py --workspace build/emulator/managed-smoke.SrdVNi`
passed against a disposable overlay of the CM4 checkpoint: private identity,
guest `id`/`uname`, upload/download byte comparison and read-only shutdown.

The continuing `forge-schema7` desktop test reached a guest-rendered desktop
after its first-boot resize/reboot, but displayed **No session for pid 1293**.
Thus schema 7 display rendering is demonstrated, while a valid login/session,
onboarding and desktop usability remain unqualified. The guest subsequently
restarted without a normal reboot message; that behavior also needs diagnosis.
Its screenshot and serial log are retained. This disposable desktop VM was
stopped through QMP without a confirmed clean guest shutdown; do not use that
stop as filesystem-consistency evidence. Its pre-test checkpoint is retained.

### 2026-09-24 PAM session and GUI image controls

Inspection through a private maintenance socket showed that the image's
`/etc/pam.d/runuser` lacks `pam_systemd`, while `runuser-l` includes it. Schema 8
uses login-mode runuser with explicit X11 session/seat hints and a private X
authority cookie. The first boot of `managed-smoke.SrdVNi` registered
`session-c1.scope` for `rpi-first-boot-wizard`, then completed its expected
resize and reboot. Post-reboot desktop usability is still being evaluated;
session creation alone is not a completed desktop acceptance test.

The GUI Image menu now starts tracked CLI lifecycle operations. Tests cover
conflicting launches, close protection, failure reporting and restore consent.
All 78 local tests passed under Xvfb; ShellCheck passed for the changed platform
helper. Installed documentation now includes the validation record and device
plan, with a staged-install regression check for both.

Maintenance diagnosis also exposed a `mount -o remount,ro /` lookup failure
when PARTUUID devices were unavailable. The runtime now resolves the actual
root block device through proc/sysfs before remounting. The diagnostic VM was
stopped after sync but without a successful read-only remount; that stop is not
counted as clean-shutdown evidence. Later display setup remounted successfully.

### 2026-09-24 initial MCP integration

`tools/validate_forge_mcp.py --workspace build/emulator/mcp-smoke.Ibe0TK`
passed using a real separate stdio client/server and a disposable CM4 overlay.
It verified initialization, denied export permissions, job polling, maintenance
boot, guest execution, upload/download byte equality, PNG capture and clean
shutdown. The guest was stopped and the server exited successfully. Its
per-client log and serial transcript remain in the workspace.

All 84 repository tests passed under Xvfb. Staged installation now verifies
that `uconsole-mcp` initializes outside the checkout and includes the companion
skill. The skill validator and ShellCheck pass. Running-job cancellation,
cross-client attachment, durable job history and a full external-agent
edit/export/reimport qualification are not covered by that smoke test.

The ongoing schema 8 desktop test displayed a text keyboard-configuration
dialog from the image's login-shell profile. Schema 9 now selects a dedicated
graphical login shell while retaining the PAM session. Its subsequent boot
rendered the graphical welcome screen, but an injected Return exposed a
competing text-mode username prompt. This does not pass onboarding acceptance.
Masking `getty@tty1.service` allowed keyboard advancement to the country page,
but did not eliminate the competing text wizard. Read-only inspection of the
official image identified `/usr/lib/userconf-pi/userconf-service`, launched by
`userconfig.service` on tty8: it calls `raspi-config nonint get_boot_cli`, which
treats our masked LightDM as console mode and runs interactive
`dpkg-reconfigure keyboard-configuration`. Desktop launch now masks
`userconfig.service` as well through the emulator-only kernel command line;
the new boot still needs full onboarding acceptance.
The physical boot configuration remains unchanged. Screenshots are retained at
`managed-smoke.SrdVNi/schema9-desktop.png` and `schema9-next.png` under
`build/emulator/`.

### 2026-09-24 installed project and firmware freshness acceptance

`make firmware` compiled the keyboard image (33,696 bytes program storage,
4,680 bytes globals) and recorded source, build-recipe, FQBN, Arduino CLI/core
and binary provenance. `make package` produced the Linux ARM64 archive using
that verified firmware. Package staging now uses unique directories; the old
root-owned stage is not reused or removed by the new build path.

`xvfb-run -a /usr/bin/python3 tools/validate_installed_workbench.py
build/uconsole-workbench-linux-aarch64.tar.gz` passed: extracted the actual
archive, launched its binary outside the checkout with fresh user data, typed
and saved a source edit through the real Tk UI, and verified that the bundled
source remained unchanged. All 94 local tests passed under Xvfb; ShellCheck and
firmware provenance verification passed. Upgrade tests preserve user project
edits and report changed bundled source without silently merging it.

This is Linux ARM64 package acceptance, not proof for other advertised hosts or
physical flashing. The schema 8 diagnostic desktop was stopped without a
confirmed clean guest shutdown to install the schema 9 adapter; its retained
checkpoint remains the recovery reference.

### 2026-09-24 image-job cancellation and export durability

MCP now accepts cancellation requests for running image subprocess jobs and
reports a distinct `cancelling` state until the worker stops. It terminates only
the job's own process group and reports that partial artifacts/journals can
remain; this is not rollback. A real subprocess cancellation test passes.
Guest execution/transfer and stop jobs still lack running cancellation. Boot
cancellation was added subsequently: callbacks check before/after artifact
refresh and during readiness waiting, and cancel an already launched boot by
stopping only its owned child under the explicit force-stop grant. The process
fixture test verifies exit, endpoint cleanup and workspace-lock release. Other
tests cover cancellation before launch (preserving previous logs), untouched
previous runtime ownership, and failed cleanup reporting failure while retaining
the owned runtime. These are controller/process tests, not clean guest-shutdown
evidence; forced boot cancellation can leave an unclean filesystem.
After this change, the system-Python suite passes all 120 tests under Xvfb
with no skips. The default PATH Python lacks Tk and skips 18 GUI tests, so
its successful run alone is not GUI qualification.

The real stdio client subsequently passed `--cancel-boot` against the stopped
disposable `mcp-smoke.Ibe0TK` workspace: it observed owned running QEMU identity
`uconsole-forge-cf0a747cdf5e45239cd66483b3f62672`, requested cancellation, and
verified terminal cancellation, process exit, private endpoint removal and
workspace-lock release. A subsequent ordinary client run passed boot, root
guest execution, upload/download byte comparison, PNG capture and clean
maintenance stop in the same workspace. This validates reuse after an early
boot cancellation, not filesystem recovery after arbitrary power loss or
desktop/native hardware boot. The prior acceptance serial log was preserved as
`prior-mcp-cycle-serial.log`; retained diagnostic VMs were not modified.
After adding retained request/response transcripts, both scenarios passed again:
`mcp-client-f9bcb63c9058491a9d5d8fd5053c6e1f.jsonl` records running cancellation,
and `mcp-client-c02462e52ef9405d9f29901e1ec50caa.jsonl` records the subsequent
round trip and clean stop. Both end with successful validation/server-exit
records. The 14-test MCP suite also passes, including rejection of acceptance
attempts that never observed an owned VM or whose boot had already completed.
Image-command output is file-backed with bounded 16 KiB response tails; the
million-byte output fixture passes. Logs are not yet durable job history.
The companion skill was updated and its validator passes. All 98 tests passed
under Xvfb, including the userconfig-mask and export-flush additions;
ShellCheck and `git diff --check` pass.
`make check-emulator` also passes the real QEMU watchdog and AXP221 identity/NACK
checks; these do not cover the planned behavioral power/battery models.

Export now flushes image data before publishing the destination hard link and
flushes the destination directory. The injected flush-failure test verifies
that a destination is not published when image data could not be flushed.

### 2026-09-24 live desktop diagnosis and onboarding progress

The combined console masks booted to serial login but had not shown usable
onboarding before the diagnostic shutdown. Offline Xorg logs confirmed
framebuffer and input initialization, while the volatile system journal was
unavailable. Schema 11 now records launcher output in the guest's
`/var/log/uconsole-emulator/desktop.log` (root-owned directory mode 0750).
The intermediate schema 10 logging attempt failed with systemd status
209/STDOUT because its output directory did not yet exist; moving directory
creation and redirection into the launcher corrected that observed failure.

A disposable diagnostic boot used the documented
[`systemd.debug_shell=ttyAMA1`](https://github.com/systemd/systemd/blob/main/man/systemd-debug-generator.xml)
option and masked the serial getty for that boot only. Its root shell was
reachable only through the owned runtime's private Unix socket, not SSH or a
physical boot default. This is a diagnostic launch, not the ordinary product
boot profile. It allowed live service inspection and installing the revised
launcher without another full boot. The workspace metadata still needs the
ordinary configure-display transaction before final acceptance.

The graphical wizard advanced through country selection and accepted test
account fields, reaching screen setup while remaining on tty7. Mouse clicks
need distinct press and release reports; the initial test's combined event
batch failed to focus the username field. Corrected USB mouse and keyboard
injection selected the field and entered `forgeproof`. The account was
subsequently verified as UID 1000, but onboarding selected console autologin
and changed the default target to `multi-user.target` because the LightDM mask
made `raspi-config get_boot_cli` report console mode. This violates the
dual-target graphical-boot contract and is not a passed gate. Schema 12 replaces
the LightDM and tty1 masks with negative kernel-command-line conditions in
dedicated unit drop-ins and removes the userconfig mask. Service enabled state
remains available to the native wizard. A new workspace,
`build/emulator/schema12-fresh`, was prepared from the hash-verified official
raw image and configured normally; fresh onboarding and reboot remain pending.
Live inspection of that fresh boot reports `graphical.target`, LightDM
`enabled` but inactive with `ConditionResult=no`, and `raspi-config nonint
get_boot_cli` returns `1` (graphical). This verifies the API-level distinction
that the earlier masks broke; it does not yet prove completed onboarding.
The optional diagnostic shell initially held ttyAMA1 while
`systemd-firstboot.service` waited to acquire that console (`StandardInput=tty`,
pre-exec process in `wait_woken`). Stopping the diagnostic shell allowed the
unmodified first-boot service to finish. A transient, boot-local unit restarted
the shell after first-boot initialization. This was a diagnostic-harness
conflict, not a reason to disable or alter the image's first-boot service.
The agent output cleaner now handles ST-terminated OSC hyperlinks from systemd,
with regression coverage. All 99 repository tests pass under Xvfb.

### Schema 13: native display-manager lifecycle

The fresh schema-12 workspace completed onboarding with `forgeproof` UID 1000,
LightDM enabled, `graphical.target` default, native display-manager symlink and
`raspi-config nonint get_boot_cli` returning `1`. Its custom launcher nevertheless
retained the wizard's old session after Launch. Schema 13 replaces that launcher
with an emulator-conditional LightDM wrapper and runtime-only Xorg/RPD overrides.
The mailbox framebuffer reports `CanGraphical=no`; the emulator configuration
therefore disables LightDM's graphical-seat check and its boot profile masks
the two absent DRM device dependencies. Native configuration is not rewritten.

Installing the new adapter into that running diagnostic guest and restarting
LightDM produced successful PAM autologin for `forgeproof`, an authenticated X
server and `/usr/bin/lxsession -s rpd-x -e LXDE` under the user's UID. This is
live session-start evidence, not fresh schema-13 onboarding or ordinary-boot
qualification. The diagnostic shell and runtime DRM masks were explicitly
applied to this test guest. The wrapper regression test verifies unmarked native
execution and refreshed autologin settings without changing the native file.

The diagnostic desktop also rendered the panel and file icons, opened a terminal
with Ctrl-Alt-T, and accepted `systemctl poweroff` through QMP keyboard input.
Unprivileged shutdown was refused because the old schema-12 wizard session was
still closing. Root `systemctl poweroff --no-block` completed userspace unmounts
and QEMU exited with status 0 through the patched AXP power-off path, without a
forced host stop. The kernel still emitted atomic-I2C and RCU-context warnings;
this is not a warning-free shutdown qualification.

After that exit, ordinary `configure-display` installed schema 13 successfully.
A managed desktop boot with no diagnostic shell or hand-applied runtime masks
started LightDM but did not render a desktop. A QEMU point-in-time full disk
backup (`startup-diagnostic.raw`) was taken from the still-running guest; its
read-only LightDM log shows Xorg PID 878 launched on VT 7 and no ready signal,
with an empty Xorg log. The diagnostic loop device was detached after reading.
This startup failure remains open: successful live restart does not establish
normal-boot readiness. Evidence remains under `build/emulator/schema12-fresh/`,
including `schema13-diagnostic-serial.log`, desktop/terminal PNGs and the normal
boot's separate `serial.log`. All 107 repository tests pass under Xvfb.

Hardware inventory now uses passive I2C sysfs enumeration, not an active bus
scan. Comparison reports failed/missing probes as incomplete, including paired
identical failures; those are not evidence of device equivalence.

Further schema-13 isolation: the ordinary desktop instance rebooted in-place
without a kernel shutdown trace. A subsequent diagnostic-shell boot started
Xorg and the user's session without a manual LightDM restart, but its shutdown
stalled after filesystem unmounts in `bcm2835_i2c_xfer`; that instance required
an explicit QMP quit and is not a clean-shutdown pass. An experiment masking
only `serial-getty@ttyAMA1.service`, without a root shell, rendered the desktop
and a duplicate PolicyKit-agent warning, then also rebooted in-place. Serial
getty masking is therefore not a demonstrated fix and was not added to the
product launch profile. Investigate the guest stall/watchdog path; service
startup and a transient screenshot do not qualify stable desktop operation.
The guest reports `RuntimeWatchdogUSec=1min` and SysRq mask `438`; debug task
dumps are not enabled by that mask. No persistent previous-boot journal was
available. Retained transcripts are `schema13-normal-failure.log` and
`schema13-debug-boot.log`; the current serial-getty-only experiment continues
logging to `serial.log` in the same workspace.

The current Linux ARM64 archive was rebuilt from staging directory
`build/ide/linux-aarch64/build.bH1se0/uconsole-workbench`. Its actual installed
launch/edit/save/immutable-bundle acceptance passes under Xvfb. An invocation on
the host desktop failed its coordinate-based edit/save assertion; that harness
is specified for Xvfb, so this does not establish native-desktop acceptance.

Watchdog investigation added a sustained-heartbeat qtest: 128 production-style
reload sequences separated by eight seconds of virtual time do not reset the
board; countdown, cancellation and eventual expiry still pass. The sequence is
based on the [Linux BCM2835 watchdog driver](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/watchdog/bcm2835_wdt.c).
Live physical-register reads during the serial-getty-only experiment showed
the counter replenished at roughly eight-second intervals over a 35-second
observation, so the watchdog is not continuously failing to receive pings.
The experiment nevertheless rebooted again. Its transcript is preserved as
`schema13-serial-mask-failure.log`. A subsequent serial BREAK plus `s` produced
`Emergency Sync complete`; BREAK plus `u` requested read-only remount but did
not produce a completion marker in the observation window and caused startup
services to encounter read-only writes. That instance was stopped explicitly;
this is not a clean shutdown or successful remount acceptance result.

The next diagnostic boot uses the ordinary desktop profile plus the temporary
`sysrq_always_enabled` kernel argument, with no diagnostic root shell or serial
getty mask. This opt-in is supported by the
[kernel SysRq implementation](https://raw.githubusercontent.com/torvalds/linux/v6.12/drivers/tty/sysrq.c)
and permits task/CPU-stack inspection through the private serial socket. It is
not part of the product launch profile or physical boot partition.
Serial SysRq stack capture is now verified on that boot. After raising only the
diagnostic console log level to 8, `w` reported no blocked tasks at uptime 131s;
`l` at 132s captured one CPU in a fontconfig filesystem check, two running
RealVNC processes (`vncserverui`, `vncagent`), and one idle CPU. This sample does
not show a kernel deadlock and does not identify the reset cause. It does show
that the private UART can collect CPU traces without a guest root service.
The kernel documentation explains the
[SysRq console log-level requirement](https://www.kernel.org/doc/html/latest/admin-guide/sysrq.html).
Keep the live instance for a failure-time trace rather than treating the
healthy sample as proof that the intermittent stall is fixed.

A subsequent failure was captured at register level: the watchdog stopped
refreshing, counted down through `0x000040ef`, then reset to `RSTC=0x102` and
counter zero as the kernel rebooted. The preceding actions included both a
terminal shortcut and a serial CPU-stack request; the trace does not isolate
which action, if either, caused the missed refresh. Two later 50-second
windows showed sustained refreshes without another reset. Do not attribute
the failure to keyboard input alone from this evidence.

`tools/observe_emulator_watchdog.py` makes this observation repeatable, checking
the runtime identity before every request and remaining read-only unless an
explicit pause threshold is provided. Regression tests cover parsing, scope
mismatch, bounds, read-only behavior and pause/register capture without resume
or quit. Its live read-only run passed, as did all 110 repository tests under
Xvfb. A bounded pause-near-expiry observation is the next failure-capture step.

### GIC monitor-read crash repaired

While the watchdog observer was still seeing regular refreshes, a separate
physical monitor read at GIC distributor address `0xff841000` terminated QEMU
with SIGSEGV (exit -11). This was a host-emulator crash triggered by diagnostics,
not a guest watchdog reset. The observer ended with a disconnected socket; it
did not pause the VM. The transcript is retained as `sysrq-before-gic-fix.log`.

A diskless `raspi4b -accel tcg -S` regression reproduced the same crash without
booting or attaching the guest image. `gic_get_current_cpu()` dereferenced the
absent `current_cpu` during monitor access. The new
`Code/patch/qemu/arm-gic-monitor-access.patch` selects bank zero for accesses
outside a vCPU, matching the existing qtest/single-CPU fallback; ordinary vCPU
accesses keep their original bank selection. The QEMU build helper applies it.
`tools/test_emulator_gic.py`, now included in `make check-emulator`, verifies
distributor identity, banked enables, CPU/hypervisor/virtual-interface reset
values and preservation of paused state under TCG. qtest alone would miss this
failure because its CPU selection already takes the fallback path.

The rebuilt QEMU passes the GIC regression, watchdog and PMIC tests;
`make check-emulator` and all 110 repository tests under Xvfb pass. A new
diagnostic boot of the same workspace is running with that binary and the
temporary SysRq argument. The GIC fix restores safe diagnostic access; it is
not yet evidence of repaired guest boot, input or shutdown behavior.

### Filesystem recovery and export guard

The next boot reported ext4 inode/block bitmap corruption and repeated service
failures. It was stopped and retained, not reused as a qualified desktop image.
The stopped overlay was flattened to `schema12-fresh/corrupt-diagnostic.raw`
for forensic inspection. A read-only `e2fsck -f -n` reported bitmap/accounting
errors while explicitly skipping journal replay, so that check alone cannot
separate pending journal work from lasting damage.

A separate `recovery-diagnostic.raw` copy was attached for automatic journal
replay/repair with `e2fsck -p` (exit 1: corrected). A subsequent forced read-only
check completed all five passes with exit 0. Both loop devices were detached;
the original overlay and forensic raw copy were not repaired. The recovered
copy is being imported into a distinct `schema13-recovered` workspace; it is
recovery evidence, not a fresh official-image acceptance run or proof that
every application file is intact. No emulator instance remains running from
the damaged workspace, and its observer ended when that owned VM was stopped.

Export now checks the converted raw image's root ext4 superblock before atomic
publication. It rejects unclean/error states, pending journal/orphan recovery
and invalid metadata checksums, retaining the source overlay. The offsets and
flags follow the [kernel ext4 superblock documentation](https://www.kernel.org/doc/html/latest/filesystems/ext4/super.html).
This is explicitly a superblock guard, not a whole-filesystem integrity claim.
It rejected the actual forensic copy and accepted the recovered copy's clean,
checksummed superblock. Tests cover clean/dirty/corrupt/truncated inputs,
no source mutation and no publication on rejection. All 113 repository tests
pass under Xvfb.

### Restore the normal early root-filesystem check

The launch command previously supplied `rw` in every mode. The guest transcript
confirmed that `systemd-fsck-root.service` skipped its check because root was
already writable. Normal and desktop launch profiles now use `ro`; maintenance
retains `rw` for explicit image edits. A regression checks all three modes.

An ordinary managed desktop launch of `schema13-recovered`, with no diagnostic
shell, SysRq override or serial-getty mask, mounted root read-only at 1.69s,
started `systemd-fsck-root` at 18.35s and completed it at 20.15s. This verifies
the repaired early-check path, not complete desktop or shutdown qualification.
All 114 repository tests pass under Xvfb. Firmware was rebuilt after the
Makefile changes (33,696 bytes program, 4,680 bytes globals) and the provenance
freshness check passed.

The old diagnostic guest's normal `systemctl poweroff` reached the kernel
power-off path, then warned about atomic I2C transfers in `axp20x_power_off`
and did not exit. It was stopped using its identity-checked QMP connection;
this does not count as a successful normal shutdown. The QEMU AXP2xx model
previously only stored writes to OFF_CTRL. The new `axp2xx-poweroff.patch`
implements the [Linux driver's bit-7 power-off command](https://github.com/torvalds/linux/blob/v6.12/drivers/mfd/axp20x.c)
at register 0x32 and bounds-checks writes to the unimplemented 0xff register.
The rebuilt QEMU passes real I2C qtests for identity, unused-address NACK,
out-of-range register handling, non-shutdown control writes and shutdown.
Existing watchdog qtests pass too. Normal guest shutdown with this model is
still unqualified; battery, charging, regulators and interrupts remain open.

### Image identity and hashes

* Official `uConsole_CM4_v3.1_64bit.img.bz2` SHA-256:
  `ef95242cdb0125e8ed08157400a26d4665acddd083481ec2314acd6e073b74ab`.
* Decompressed raw image SHA-256:
  `a7b0a2bfa86a45150af1ae70a94ed432718bfec90ffdef54952564bd34f0d788`.
* Shipped `kernel8.img` SHA-256:
  `4871c5bbb93f24aadfc574eb6af392464f11bbc4f0eb50e1b40eff3e78d67276`.
* Guest identifies itself as Debian GNU/Linux 13.2, kernel `6.12.62-v8+`,
  AArch64, Raspberry Pi Compute Module 4 device tree.

## Observations

* The shipped kernel mounts the real ext4 root filesystem from an 8 GiB virtual
  SD overlay. No replacement distribution or generic `virt` kernel is involved.
* The maintenance shell accepts commands over the emulated PL011 UART.
* After the RAM fix, `/proc/meminfo` reports `MemTotal: 1902268 kB`.
* Linux enumerates QEMU USB keyboard and relative mouse devices through DWC2.
  QMP keyboard injection delivered Linux SysRq sync, remount and poweroff
  requests in the normal guest. This does not validate STM32 keyboard firmware.
* The runtime tree contains an enabled `/soc/i2c@7e205000/pmic@34`. After the
  shipped I2C driver module loads, Linux reports `AXP20x variant AXP221 found`
  and binds `22-0034`. The qtest harness independently reads chip ID `0x06`
  through the emulated BCM2835 I2C controller and observes a NACK at an unused
  address. Battery state, charging, regulators, ADC and PMIC interrupts remain
  outside this result.
* Normal systemd boot completes the image's first-boot resizing/setup and its
  requested reboot. A subsequent boot reaches `clockworkpi login:` on ttyAMA1.
  It acquires `10.0.2.15` through the optional USB network substitute, and host
  port 2222 returns `SSH-2.0-OpenSSH_10.0p2 Debian-7`. Authenticated SSH was not
  established; no image account/password was changed.
* The EEPROM updater fails without real EEPROM hardware. Several physical
  GPIO/regulator devices remain deferred, and hardware-dependent services can
  delay boot/shutdown. A login prompt does not imply all services are healthy.
* A host Markdown file was copied into `/root/emulator-guide.md`, checked with
  SHA-256 in the guest, synced, and exported to a new raw SD image. Booting a
  new overlay of that exported image retained the same file checksum:
  `d391d7c52b7cc00b159137a8672a6316b78302926c5ad137fa1c4c41b5786e31`.
  This records the content at upload time; the host guide has since been edited.
* The exported image SHA-256 was
  `2e9d72879cd4b29fa1be863d2ff85c0e0a4e89401d44c1725f8059b53bcf13ba`.
  An attempted export while QEMU held the disk failed on its image lock.
* QMP status, pause and resume work. The workbench editor/copy test created
  `/root/gui-proof.txt` and read it back with SHA-256
  `a06f8425622ce728e62499f0cf8d72b0eb727b6556f2606b838814b8c7132a9b`.
  GUI tests also verify that selected boot output and clean agent context reach
  the desktop clipboard.
* On 2026-09-24, a fresh writable overlay was configured for the display
  surrogate without changing its raw backing image. The guest registered a
  1280x720, 32-bit mailbox framebuffer. Desktop mode then started Xorg with the
  `fbdev` driver on `/dev/fb0` and launched the image's own RPD X session for
  its configured first-boot user. A QMP screendump is exactly 1280x720 and
  shows the Raspberry Pi first-boot desktop. This validates guest-rendered X11
  pixels and input cursor scanout, not VC4/V3D, DSI, panel timing, rotation,
  backlight or GPU acceleration.

## Checks and evidence

The repository suite runs 49 tests in its normal headless invocation: 38 pass
and 11 GUI tests skip without a display. The five workbench GUI tests and six
modem GUI tests also pass under Xvfb, including the clipboard path. ShellCheck
passes. Separate QEMU
qtest harnesses exercise watchdog arm, countdown, reload, cancel,
password rejection, expiry/reset and Linux poweroff, plus AXP221 I2C identity
and NACK handling.

Generated local evidence (ignored by Git):

| Path under `build/emulator/` | Evidence |
| --- | --- |
| `qemu-configure.log`, `qemu-build.log` | Baseline QEMU configuration/build |
| `qemu-watchdog-build.log`, `qemu-memory-build.log` | Device-model fixes built |
| `watchdog-test.log` | Real QEMU register/timer tests |
| `pmic-qemu.log`, `export-smoke/serial.log` | Linux AXP221 probe and I2C binding |
| `maintenance-evidence.log` | Guest OS, kernel, RAM, input devices |
| `normal-firstboot-patched.log` | First-boot setup and requested reboot |
| `workspace/serial.log` | Subsequent normal login, network, USB SysRq poweroff |
| `upload-evidence.log`, `export-evidence.log` | Guest file upload and raw export |
| `workbench-smoke.log`, `workbench.png` | Desktop workflow and screenshot |
| `export-smoke/serial.log`, `export-smoke/transfer.log` | Exported-image boot, GUI file copy/readback |
| `repo-tests.log`, `all-gui-tests.log` | Repository tests and Tk checks |
| `base-preservation.log` | Original raw image and backing-image hashes |
| `surrogate-display.png` | 1280x720 guest-rendered RPD first-boot desktop |
| `surrogate-display-serial.log` | Framebuffer registration and desktop boot history |

The redundant scratch raw image was removed after the preservation check;
the working workspace's backing image and exported SD image are retained.

macOS/Windows boot, accelerated graphics, GDB interaction, physical CM4
comparison, keyboard flashing, modem, battery, charging, audio and DSI/panel
behavior remain unvalidated. Host-tool CI is configured for three operating systems;
it has not been pushed/run remotely as part of this local work.
