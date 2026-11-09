# Audio modes and evidence

Check the connected server's tool schema first; older installations may not
support every mode. Select `audio` in `boot` for a stopped workspace. Do not
restart a user's running guest merely to change its audio mode.

| Mode | Behavior |
| --- | --- |
| `none` | Default: no audio surrogate attached |
| `usb-null` | USB playback discarded by a null backend |
| `usb-wav` | USB playback recorded to a private workspace WAV |
| `usb-capture` | Synthetic stereo S16_LE 48 kHz input; no host microphone |
| `usb-duplex` | Two USB cards: private-WAV playback and simultaneous synthetic capture |

None models native jack/speaker circuitry. Duplex is two separate ALSA cards,
not a native full-duplex codec. Capture's two channels encode the low/high
halves of a uint32 frame counter, starting again on stream activation; this is
diagnostic PCM, not a listening tone or a real microphone recording.

Boot requires the owner's `boot` and `force-stop` grants. For an attached
Workbench session whose registered workspace is `gui`, a capture boot request is:

```json
{"name":"boot","arguments":{"workspace":"gui","mode":"maintenance","audio":"usb-capture"}}
```

For concurrent playback and synthetic input instead:

```json
{"name":"boot","arguments":{"workspace":"gui","mode":"maintenance","audio":"usb-duplex"}}
```

These are `tools/call` parameters, not success responses. Poll the returned
job, then use the maintenance guest command/transfer tools only if separately
granted. Their limits still apply: `guest_exec` accepts at most 2200 characters;
upload a longer probe beneath the approved files root instead of bypassing the
command limit. Keep guest exit-code checks separate from job completion.

Query the selected surrogate's attachment:

```json
{"name":"audio_query","arguments":{"workspace":"gui"}}
```

The result describes QEMU presence/connection, not guest ALSA readiness. Capture
reports `capture: true` and `host_microphone: false`. If the task requires hotplug,
the owner must grant `device-control` separately:

```json
{"name":"audio_set","arguments":{"workspace":"gui","connected":false}}
```

```json
{"name":"audio_set","arguments":{"workspace":"gui","connected":true}}
```

Wait for each job before the next mutation. These jobs are not cancellable;
failure can leave an uncertain or partial device change, not a rollback. Inspect
the result and owner state before retrying. Verify Linux device disappearance,
reappearance and actual I/O separately when claiming hotplug recovery.

Duplex `audio_set` operates on both devices in sequence, not atomically. Inspect
the `devices` map (`audio-surrogate` playback, `audio-capture` input) after a
failure. The aggregate `present`/`connected` fields are true only when both
devices satisfy that state; false does not imply both are absent. Reconcile
observed state explicitly before requesting another mutation. Claim concurrent
I/O only with overlapping guest playback/capture activity and validated data
in both directions, not two sequential successful commands.

For `usb-wav` or `usb-duplex`, use the recording path returned by the boot result. Finalize it
by cleanly stopping the VM before reading its header/sample count. Do not invent
an output path or regard the file's existence as recording success. Recordings
consume disk and can contain private guest audio; they are not release assets
without review. Backend write-error logging is not a guarantee of automatic GUI
failure reporting, and recording gaps are not wall-clock continuity evidence.

Stock guest playback can report success while losing final samples. A candidate
guest USB audio drain fix has passed focused tests but is not automatically
installed. Do not silently install a kernel/module workaround or infer lossless
output from `aplay`'s exit status. Verify the samples required by the task and
report the exact guest/module identity. Synthetic capture has passed with the
stock driver; verify the sequence rather than merely a nonempty capture file.

Audio selection and hotplug are host-side test configuration, not durable
modifications to the guest image. Preserve native boot defaults when making
guest changes, and distinguish emulator test results from real-device evidence.
