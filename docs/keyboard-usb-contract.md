# Keyboard composite-device implementation baseline

This is build-derived evidence for M4, not a hardware capture or a completed
emulator device. The current generic QEMU keyboard/mouse remain substitutes.

`Code/uconsole_keyboard/uconsole_keyboard.ino` passes one explicit concatenated
HID report descriptor to USBComposite, then registers CDC serial in the same
device. The pinned STM32F1 2021.2.22 core supplies USBComposite; its archive
identity is documented in the [firmware build instructions](../Code/uconsole_keyboard/README.md).
The application sets manufacturer `ClockworkPI`, product `uConsole`, and serial
`20230713`. HID registration precedes CDC registration. Endpoint numbers and
the final configuration descriptor still need runtime/capture qualification;
do not substitute generic-device descriptors as evidence of a match.

Extract the actual compiled report descriptor without running MCU code:

```sh
python3 tools/keyboard_usb_contract.py build/firmware/uconsole_keyboard.ino.elf \
  --output build/emulator/keyboard-usb-contract.json
```

The output is created exclusively, never overwritten. It records the ELF and
descriptor SHA-256 hashes, exact descriptor bytes, and a non-hardware evidence
label. The tool accepts only an unstripped little-endian ARM ELF32 executable
with one allocated, file-backed `reportDescription` object. It rejects missing,
duplicate, truncated, or out-of-bounds descriptor data. It does not establish
firmware provenance by itself; keep the firmware build's provenance manifest.

The locally inspected build has a 219-byte descriptor with SHA-256
`351629b59fd36f6dc0b2aea094a207e232e668a0eff8a5bb38caa2d9a7c297b7`.
Its descriptor and USBComposite definitions specify:

| Function | Report ID | Payload excluding report ID | Direction |
| --- | --- | --- | --- |
| Consumer controls | 3 | One 16-bit usage, maximum `0x3ff` | Input |
| Keyboard | 2 | Modifier byte, reserved byte, six key usages | Input |
| Keyboard LEDs | 2 | Eight LED bits | Output |
| Joystick | 20 | 32 buttons, four-bit hat, six ten-bit axes/sliders: 12 bytes | Input |
| Mouse | 1 | Eight button bits, signed X/Y/wheel bytes | Input |

Descriptor order is consumer, keyboard, joystick, mouse. The joystick's hat
declares a null state; mouse axes are relative. These details must survive in
the composite model instead of being reduced to boot keyboard/mouse reports.

An experimental QEMU transport now implements the initial configuration/control
path and report queues described below. Remaining implementation includes
firmware-derived matrix/layer and trackball integration, lighting, full transport
error/timing fidelity, bootloader re-enumeration, and update failure scenarios.
Matching these compiled bytes will not alone prove those behaviors or match an
independently flashed physical keyboard.

## Experimental QEMU composite transport

The pinned QEMU build includes `usb-uconsole-keyboard`, introduced by
`Code/patch/qemu/uconsole-keyboard.patch`. It is deliberately **not the default
Workbench input device**. A direct QEMU launch can attach it with
`-device usb-uconsole-keyboard,id=deck,port=1` on an available USB port.
The normal launcher still uses generic keyboard/mouse substitutes.
The emulator CLI also accepts `run --keyboard composite` to select this device
without generic input; the option cannot be combined with the split
`--keyboard-cdc-port` surrogate. GUI and MCP boot defaults remain unchanged.

The model supplies the compiled 219-byte report descriptor, ClockworkPI/uConsole
strings, serial `20230713`, and three interfaces. Its 100-byte configuration uses
the inspected core's HID-first/CDC-second allocation: HID interrupt IN `0x81`,
CDC notification IN `0x83`, and CDC bulk OUT/IN `0x02`/`0x82`. The layout is
source-derived and exercised in QEMU, **not compared with physical enumeration**.
The QEMU interface table and on-wire configuration are separate representations;
the DMA regression checks the complete returned configuration bytes.

QOM property `inject-report` accepts exactly one hex-encoded input report,
including its ID; `transport-state` exposes queue depth, LEDs, CDC receive count,
line controls and a reset-request flag. Injection requires configuration 1 and
a free slot in the bounded 32-report queue. Invalid IDs, lengths, hex and overflow
are rejected before state changes. This is a transport test hook, not host-key
mapping or a Workbench/MCP control. The queue policy is not yet a timing/byte-ring
model of USBComposite. Migration is explicitly unsupported.

The current CDC path supports line coding and DTR/RTS controls, receives bytes
without echo, and NAKs when the firmware's unconsumed RX threshold is reached.
It detects the two source-derived reset sequences. **A reset request currently
sets a diagnostic flag only:** it does not disconnect, enumerate a bootloader,
run DFU, or claim a successful flash. USB bus reset clears transport queues and
configuration, while retaining line-coding/DTR state as in the inspected core.

Run `python3 tools/test_emulator_keyboard.py` after rebuilding QEMU, or use
`make check-emulator`. The qtest exercises real CM4 DWC2 control/interrupt/bulk
DMA: complete descriptors and strings, all four input report IDs, invalid/full
queue rejection, ordering, empty-queue NAK, LEDs, CDC control-interface scoping,
line coding, bus reset, RX backpressure, both reset requests and non-triggering
unarmed/split magic sequences. This qtest does not prove Linux driver binding,
desktop input, exact MCU behavior or any hardware fidelity. The separate
real-guest validator below checks the Linux driver/input boundary.

### Real Linux guest acceptance

`tools/validate_keyboard_guest.py --image RAW --sha256 SHA256 --output NEW_DIR`
creates a small disposable overlay backed read-only by the verified image,
boots the stock kernel in maintenance mode with composite input, and loads
`usbhid`/`cdc_acm` from that guest. It copies a UUID-named probe into guest `/tmp`.
The probe verifies interface-driver bindings and identity, changes CDC termios
to 9600 baud, and opens only evdev nodes belonging to this emulated USB device.
A per-run serial readiness marker precedes host report injection. Host validation
requires actual keyboard, relative mouse/wheel, volume and joystick evdev events;
a zero probe exit code or empty `missing` list alone cannot establish success.

Successful acceptance removes the exact temporary probe, cleanly stops the VM,
checks the root primary ext4 superblock and rechecks the base hash. Logs, report
bytes, observed events, driver bindings, cleanup status and the overlay remain
in the output directory. This is a Linux application/driver boundary test,
not desktop focus/interaction, physical comparison, firmware state-machine
integration or DFU qualification. No guest packages or permanent guest services
are added. The backing image is not rewritten.

### Compiled transport templates

Add `--usb-templates` to extract the ELF's pre-initialization device descriptor,
configuration header, HID part and CDC part. Each object records its symbol,
length, exact bytes, hash and descriptor boundaries. Missing symbols, unexpected
object sizes and invalid descriptor boundaries are errors. The original ELF hash
covers all objects. Default output remains the HID report descriptor only.

The inspected ELF is SHA-256
`df83700173c1fc180d59c8b01b710dc91e244c55af0f3c87f61edb6de5525c67`.
Its template sizes are 18, 9, 25 and 66 bytes respectively. The device template
contains VID:PID `1eaf:0024`, USB/device revision `0200`, and EP0 packet size 64.
The HID part declares subclass 1, protocol 0 and a 64-byte interrupt IN endpoint
with interval 10. The CDC part contains an IAD, control and data interfaces,
16-byte interrupt IN notification endpoint (interval 255), and 64-byte bulk OUT
and IN endpoints. Preserve the template's actual functional descriptor bytes;
do not normalize them to another generic CDC implementation.

These are **not final enumeration descriptors**. Endpoint numbers are zero in
the templates; the core allocates them when HID and then serial register.
Interface numbers and CDC cross-references, HID report length, packet sizes,
configuration size/count and string indices are patched at startup. In
particular, the device template's zero serial index does not mean the running
keyboard lacks its `20230713` serial. The output explicitly marks
`runtime_initialized: false` and lists unresolved fields. RAM-initialized `.data`
objects are extracted from their ELF file backing, never from a running device.

The inspected pinned core also provides two CDC reset paths under `SERIAL_USB`:
a DTR falling edge at 1200 baud initiates a watchdog reset; otherwise the receive
hook following that edge checks the last four available bytes for `1EAF`.
The edge state is consumed by that receive hook even on a short/mismatched
message. Both hooks are present in this ELF's symbol table. The repository's
`upload-reset.c` uses DTR/RTS changes and the magic bytes. These source-derived
requirements inform the upcoming CDC model, but do not prove an actual reset,
bootloader identity, re-enumeration or successful DFU upload.

## Host firmware behavior oracle

`make keyboard-oracle` builds `build/keyboard-oracle/keyboard-oracle` with a
host C++17 compiler. This executable includes the checked-in firmware `.ino`
files directly, including `setup()`, `loop()`, matrix/direct-key scans, keymaps,
state, rate meter and trackball glide. The firmware sources are not rewritten
or replaced by a second mapping implementation. This is a development test
oracle for the future composite device, not an emulator input backend yet.

The boundary in `tools/keyboard-oracle/` provides logical pins, a deterministic
microsecond clock and recorders for the USBComposite methods. API key and
consumer constants match the pinned core's `USBHID.h`. USB initialization uses
explicit placeholder descriptors; **no emitted record is a USB report**.
Use the ELF extractor above for actual descriptor bytes. USBComposite's ASCII
translation, six-key rollover, LED output, report packing, endpoint queues, CDC
and reset behavior are not implemented by this oracle.

Input is one command per line, with zero-based indices:

| Command | Effect |
| --- | --- |
| `matrix ROW COL 0-or-1` | Set one of 64 logical matrix contacts |
| `key INDEX 0-or-1` | Set direct key index 0–16, in firmware `keys_io` order |
| `switch 0-or-1` | Set PD2 mode switch (initially 1) |
| `run COUNT` | Run 0–10000 complete production `loop()` iterations |
| `advance MS` | Advance virtual time 0–1000000 ms without scanning |
| `edge DIRECTION` | Invoke a registered trackball interrupt: 0 left, 1 right, 2 up, 3 down |
| `state` | Record Fn, keyboard lock and backlight state |

Each process begins at reset and runs production setup. Delays advance virtual
time without sleeping. Matrix reads combine closed contacts with the selected
column; there is no electrical ghosting or GPIO propagation model. Edges occur
between commands, not asynchronously inside scans; these are logical interrupt
events, not a complete quadrature waveform model. Timer lock interrupts remain
disabled as in the current firmware. Host floating-point behavior is not proof
of bit-identical MCU arithmetic. Invalid commands fail with exit status 2.

For example, press/release Q through the actual matrix debouncer:

```sh
printf 'matrix 3 0 1\nrun 10\nmatrix 3 0 0\nrun 10\n' |
  build/keyboard-oracle/keyboard-oracle
```

Stdout is JSONL, beginning with the `host-firmware-semantic-oracle` evidence
label. Calls contain virtual `us`, an `event` and three integer arguments;
unused arguments are zero. Initialization calls are retained. Keyboard arguments
are USBComposite API values (ASCII or its special-key constants), not Linux
keycodes or HID usages. PWM pin IDs are local symbolic identifiers, not STM32
register addresses. Preserve the source revision with any recorded trace.

`tests/test_keyboard_oracle.py` compiles a fresh oracle and tests populated
character matrix positions, empty positions, press/hold/release, matrix/direct
bounce, Fn release identity, keyboard locking, Caps adjustment calls, all 17
direct keys, consumer controls, lighting and deterministic trackball movement
and Select scrolling. It also preserves a potentially surprising production
behavior: PD2 changes game-button output, but the current D-pad map always uses
arrow keys, not joystick axes. The composite model must not silently implement
a different map based on comments or intended behavior. Hardware traces and
USB report-level comparisons remain required for M4 acceptance.

## Firmware trace to HID reports

`tools/keyboard_reports.py` converts the oracle's JSONL into timestamped HID
report bytes. It validates the whole bounded trace before returning any output.
The scanner/keymaps remain production firmware; the encoder implements the
pinned USBComposite report methods separately. Its ASCII lookup table was
extracted from the same compiled ELF (`_ZL12ascii_to_hid`, 128 bytes, SHA-256
`52369ccefa4602cc7e0143b423762911c89c830c9ca9ef6e099178b470b0c3d5`).

```sh
printf 'matrix 4 2 1\nrun 10\nmatrix 4 2 0\nrun 10\n' |
  build/keyboard-oracle/keyboard-oracle |
  python3 tools/keyboard_reports.py
```

This produces A press/release plus the firmware's initialization joystick
reports. Encoding retains the core's first-free six-key slots, silent seventh-key
refusal, duplicate keyboard reports, non-reference-counted Shift release,
mouse-button change suppression and click overwrite, consumer release and
packed ten-bit joystick axes/defaults. `--leds` supplies an initial LED byte;
it is not live host feedback. Non-report state/PWM events remain in the original
oracle trace but do not become HID reports.

`tools/validate_keyboard_reports.py --core USBComposite_SOURCE_DIR --elf ELF`
compiles the selected core's actual `Keyboard.cpp`, `Mouse.cpp`, `Consumer.cpp`
and `Joystick.cpp` method bodies against a host layout/send shim. It compares
their complete output with the encoder across all byte-valued keys, Caps LED
states, and deterministic overlapping keyboard/mouse/consumer/joystick events.
The shim replaces construction/layout and USB sending, not those method bodies;
this is still not MCU execution or transport timing. Optional `--output FILE`
creates exclusive JSON evidence with source, shim, encoder and stream hashes.

`test_emulator_keyboard.py --oracle build/keyboard-oracle/keyboard-oracle`
feeds an actual firmware trace through the encoder and QEMU report injection,
then compares DWC2 DMA reads byte-for-byte. `make check-emulator` now builds the
oracle and includes this test. The replay drains each report before the next;
timestamps are retained as oracle evidence, not used to claim USB polling-rate
or real-time fidelity. Live host key/pointer integration, ongoing LED feedback,
and desktop testing of this firmware-driven path remain open.

## Persistent firmware bridge

`forge_keyboard.KeyboardBridge` owns a long-lived oracle process for an
explicitly selected composite VM runtime. Separate logical matrix/direct-key
actions retain firmware and encoder state. Each request uses a private sync
marker; commands and queue delivery are bounded, and host LED state is sampled
through the owned transport. A USB reset, changed runtime owner or uncertain
delivery invalidates the bridge rather than silently replaying input. Closing
it stops only its oracle child, not the VM, and does not claim held keys were
released. This is explicit virtual firmware time, not MCU/USB timing emulation.

The live guest validator accepts `--oracle build/keyboard-oracle/keyboard-oracle`
to select a separate firmware profile. Six actions exercise A press/release and
Fn-selected F1 with Fn released before the selected key. The profile requires
ordered Linux key transitions; it does not replace the broader raw-report test.
Controller/MCP input is available, with the explicit GUI deck described below.
The `--caps-feedback` validator profile also checks a live Linux LED round trip:
Fn+Tab generates Caps Lock, the guest sends LED value 2, and a subsequent A press
while Caps is held receives the production firmware's Shift adjustment. The
validator requires both the sampled LED and exact report bytes, as well as
Linux Caps/Shift/A events. This does not qualify every LED combination.
Firmware-driven pointer/gamepad desktop interaction, host-event mapping and
bootloader lifecycle remain open.

### Workbench deck controls

Select `composite` under **Next boot input**, boot the VM, then open
**Keyboard deck**. This explicit logical-contact panel uses the shared controller
and bundled oracle. Click a matrix/direct control once to hold it and again to
release it; the held indicator changes only after a successful job. The grid is
matrix wiring order, not a scale drawing of the physical keyboard. Up to 32
contacts can be held through the panel; release those holds before closing it.
Mode switches and individual trackball edges also run through production firmware.

The panel installs no global/editor keyboard bindings. Click its dedicated
typing area to send host key events through US-keyboard logical contacts.
Letters, digits, punctuation, arrows, Shift/Ctrl/Alt and F1–F12 are mapped;
Menu acts as Fn. Function-key mappings settle Fn before pressing the character
contact. Release uses the original host key identity even if its keysym changes.
Repeated presses and adjacent X11 repeat release/press pairs are suppressed;
guest key repeat remains guest policy. The adapter maps host symbols, not host
electrical scan timing or arbitrary keyboard layouts.

Typing actions queue behind in-flight input; focus loss queues release of the
typing area's contacts. Do not mix held clickable deck controls and host typing.
Queue overflow or uncertain delivery disables further input and requires
inspection/stopping the VM, rather than claiming a release succeeded. Host
typing has focused mapping/Tk tests and a live stock-Linux A/F1/focus-loss test
using generated Tk events. Physical host typing, non-US layouts and high-rate
input still need broader qualification.
The dedicated pointer pad accepts movement only while focused. Its first click
focuses the pad without clicking in the guest; subsequent left/middle/right
clicks use direct contacts 13/16/15. Four host pixels produce one trackball edge,
with fractional movement retained and fixed virtual scans between edges.
Production acceleration determines resulting HID motion, so this is not a
pixel-for-pixel pointer or a measured physical timing model. Batches remain
within the bridge command limit; a single jump over 128 edges or queue overflow
stops input rather than silently dropping motion. Leaving the pad resets its
coordinate origin; losing focus releases its held buttons. A generated-Tk-event
test now verifies positive X/Y motion and all three button pairs in the stock
Linux guest, including right-button release on focus loss. Physical host mouse
capture and desktop click-target acceptance remain open.

Wheel input is deliberately opt-in via **Enable Select-scroll**. Current
production firmware immediately emits Space while Select is held in keyboard
mode (or game button 9 in game mode). The adapter preserves that side effect:
it holds Select, delivers two vertical trackball edges per wheel notch, then
releases Select. It does not filter keyboard reports to pretend the firmware
behaves differently. Release other held contacts before scrolling. X11 wheel
buttons and Tk MouseWheel events are accepted only on the focused pointer pad;
individual actions are limited to eight notches. Linux/X11 generated-event
acceptance verifies both directions and the Space pairs. Native macOS/Windows
wheel conventions and game-mode scrolling remain unqualified.

Indicators track only actions from
this panel; external MCP clients share firmware state and may hold other keys.
Do not interleave independent input sources when interpreting those indicators.
An uncertain action disables further panel input; inspect its job evidence and
stop the VM before closing/recreating that deck. Unit GUI checks cover successful
hold/release, failed actions, changed VM identity and absence of global bindings;
the six-button GUI-to-Linux path also passes in the stock maintenance guest.
Live desktop application and host-event mapping qualification remain open.
