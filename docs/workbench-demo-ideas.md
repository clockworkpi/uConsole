# Cool demo material: live uConsole schematic

Status: implemented, requested 2026-09-27. Not part of the
published v1.1.1 help/diagnostics release.

The Live Schematic toolbar button opens a Canvas
component map, source-symbol navigation, search, pan/zoom and a text inspector.
Sources open as inspection copies in the Host editor; saving requires choosing
a destination, and unsaved edits are protected. The model distinguishes unknown,
stale and identity-scoped observations. A bounded background collector now reads
owned-runtime status, power and USB state, plus storage byte counters. Storage
counter increases and sampled power changes drive activity; USB attachment
alone does not. Record/stop/save/replay controls retain bounded observation
traces and replay them without dispatching hardware operations. Replay is
explicitly labeled and isolated from live collection.

SD successful-read/write byte counters, keyboard HID delivery and framebuffer redraw counters are implemented as
read-only QOM properties in the patched QEMU build. They count actual successful
SD I/O, USB IN transfers and redraw notifications; redraws can include
host invalidation, not only guest rendering. A real-QEMU qtest check verifies
counter changes, idle/queued-input behavior, write rejection and trace replay.
This is device-level evidence, not a Linux guest walkthrough.

Open schematic sheet provides offline rendered PDFs, sheet selection, pan/zoom,
component/net text search, functional code links and source-PDF SHA-256 provenance.
`tools/build_schematic_assets.py` regenerates the checked assets using Poppler;
the installed application needs no PDF renderer. The shared carrier's printed
AXP228 maps to the Linux/QEMU AXP221 identity, not a claim of a different chip.

Package checks cover source navigation (including bundled QEMU model/patch
code), offline schematic sheets and visibly labeled replay. The running-guest
walkthrough verifies firmware-to-Linux A/F1 events, guest AC-driver state,
framebuffer changes, SD activity, replay, clean shutdown and unchanged backing
image hash. It retains a screen recording and raw observation trace. Earlier
failed walkthrough evidence is retained, including the finding that generic
block statistics do not account for this SD model.

## Running and qualifying the demo

1. Click Live Schematic. It also works without a guest for
   source navigation, schematic inspection and recorded playback.
2. For live keyboard activity, select composite keyboard before booting the
   guest. Use Keyboard deck to exercise the firmware-backed input path. Use the
   existing power controls for reviewed state changes; diagram selection never
   actuates hardware. Build the current patched QEMU for SD/HID/redraw counters.
3. Record observations, perform your walkthrough, stop and save to a new JSON
   file. Replay recording is a visualization, not a power/input macro.

`tests/test_workbench_schematic*.py` cover source resolution, PDF provenance,
Tk navigation, unsaved edits, stale/ownership rules, bounded high-rate sampling,
responsive editing, recording limits and replay validation. The native archive
validator checks the same packaged source/sheet/replay UI outside the checkout.

`tools/test_emulator_observations.py --output NEW_DIRECTORY` tests actual USB
DMA and framebuffer counter behavior. `tools/validate_schematic_guest.py
--image IMAGE --sha256 SHA256 --output NEW_DIRECTORY --video` exercises the
Workbench and Linux guest using an exclusive disposable overlay. Run it with
Tk, patched QEMU, the keyboard oracle and (for video) ffmpeg available; video
expects a 1900×1000 X display. It preserves the backing image and retains jobs,
guest-input proof, framebuffer captures, observations and a video. It explicitly
requests framebuffer captures for evidence, which can themselves trigger a
redraw notification. This is not proof of physical DSI/GPU fidelity.

## Experience

A zoomable, pannable Tk Canvas depicts the uConsole's components and buses.
Clicking a component opens the corresponding implementation in the Host source
editor, at the relevant symbol. While an owned emulator runs, the diagram shows
observed state and activity: keyboard events, USB attachment, power changes,
display updates, storage traffic and modem/audio events where instrumented.

Start with a readable system diagram; allow drill-down into schematic sheets
and component/net detail. Use the repository's CM4 adapter, mainboard, keyboard
and 4G schematics as the hardware references, with explicit sheet/revision
provenance. Do not substitute a decorative board drawing for that mapping.

## Implementation outline

- Canvas vector items, tagged by stable component/net IDs, provide hit testing,
  selection, tooltips and highlights without requiring a web renderer. Provide
  keyboard navigation, a searchable component list and a text inspector too.
- A declarative component map binds hardware reference designators and buses to
  source paths/symbols, emulator device IDs, fidelity labels and telemetry keys.
  It supports one-to-many mappings: real hardware, its QEMU surrogate and the
  guest driver are not necessarily the same implementation.
- Source navigation must preserve unsaved edits. Inspect installed sources
  read-only or copy them into a user-owned project before editing; never make
  package resources writable just to support a demo. Resolve symbols against
  the actual source revision rather than relying only on brittle line numbers.
- Reuse the existing owned runtime/controller for read-only observations. Power
  already has QOM-backed sampling; audio has owned-device attachment readback;
  the modem has runtime query/inspection. These establish state, not bus traffic.
- Add bounded QEMU trace/counter instrumentation for actual transaction activity
  where needed. Coalesce samples off the UI thread and apply updates on Tk's
  thread. Cap update rates, queue sizes and history so a busy guest cannot freeze
  the editor or delay its control jobs.
- Selecting a component is navigation, never an implicit power change, device
  write or physical-target action. Optional actions call the existing guarded
  controls with their normal permissions and confirmations.

## Honest runtime presentation

Distinguish **present**, **configured**, **recent observed activity**, **fault**,
**stale/disconnected**, and **not instrumented**. Show timestamps, sample source,
and dropped-event counts. Use labels/shapes as well as color. A running VM does
not imply that every connected device is active. Missing telemetry is not idle.
Reset highlights on stop/restart and bind observations to the owned VM identity.

Brief pulses may visualize observed events; their visual duration is for human
readability, not hardware timing. Mark host surrogates prominently. This is a
live, code-linked functional view—not SPICE, electrical simulation, or evidence
of native DSI/GPU, signal-integrity or real-device fidelity.

## Demo milestones

1. **Clickable map:** CM4, PMIC/battery, keyboard/trackball, display, storage,
   audio and modem, with verified source links, pan/zoom and accessible selection.
2. **Live state:** owned-runtime status and existing power/USB/modem readbacks;
   explicit unknown/stale states when a device lacks telemetry or disconnects.
3. **Activity and replay:** instrument selected bus/device events, then record and
   replay a deterministic walkthrough: press a key, follow the observed event
   path, jump to its code, change a power state, and inspect the resulting guest
   behavior. Label replay distinctly from live observations.

Acceptance includes source-link tests across packaged/check-out installations,
unsaved-edit protection, visible unknown states, stop/restart ownership tests,
synthetic high-rate telemetry stress, responsive editing during updates, and a
recorded demo whose highlights can be reconciled with retained trace evidence.
