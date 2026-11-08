# Framebuffer mailbox alias and visible firmware output

The property-mailbox allocation reply is a VideoCore bus address. The
framebuffer model internally uses the RAM offset, while the mailbox wire
response must select the canonical `0xc0000000` GPU alias. QEMU already maps
that alias in its GPU bus address space; no extra RAM mapping is required.

The unmodified reply returned `0x3c100000` in the tested Pi 4 configuration.
The pftf UEFI firmware subtracts `BCM2836_DMA_DEVICE_OFFSET` and therefore
selected CPU address `0x7c100000`. Firmware and kernel console writes reached
that RAM, while QMP displayed the separate buffer at CPU `0x3c100000`. A black
screendump consequently hid an actual FreeBSD panic; it did not establish
that firmware or the kernel had stopped executing before console setup.

The [mailbox patch](../Code/patch/qemu/bcm2835-framebuffer-bus-alias.patch)
changes only the allocation reply to `internal_base | 0xc0000000`. In this
configuration the reply becomes `0xfc100000`; EDK2 subtraction and Linux
upper-bit masking then both select CPU `0x3c100000`. Internal framebuffer
state and bus mappings remain unchanged.

The fix is adapted, with attribution, from Frederik van Hövell's
[2026-08-19 QEMU-devel submission](https://www.mail-archive.com/qemu-devel@nongnu.org/msg1217399.html).
That submission cites immutable
[EDK2 address conversion](https://github.com/tianocore/edk2-platforms/blob/306137738faf9f31eba9823d0f818397755cedaf/Platform/RaspberryPi/Drivers/RpiFirmwareDxe/RpiFirmwareDxe.c#L1045),
[upstream Linux interpretation](https://github.com/torvalds/linux/blob/0031c06807cfa8aa51a759ff8aa09e1aa48149af/drivers/gpu/drm/vc4/vc4_drv.c#L312)
and the [Raspberry Pi Linux framebuffer driver](https://github.com/raspberrypi/linux/blob/3e44c7fdf879208adf51be589d60962269d25d18/drivers/video/fbdev/bcm2708_fb.c#L464).

## Independent display regression

Run the test against the newly built patched binary:

```sh
python3 tools/test_emulator_framebuffer_alias.py \
  --qemu build/emulator/qemu-build/qemu-system-aarch64 \
  --output /absolute/private/path/new-framebuffer-check
```

This sends real property-mailbox requests, obtains the allocated address and
pitch, performs CPU writes using EDK2's subtraction, and checks exact red,
green and blue pixels in a QMP PPM screendump. It checks normal and canonical
GPU-alias request-buffer addresses, then repeats after a system reset. It
also verifies that pixels outside the three written pixels remain black.
Screenshots are captured from QEMU; they are not synthesized from the test
pattern or rewritten after capture.

For the preserved old binary, the explicit negative control is:

```sh
python3 tools/test_emulator_framebuffer_alias.py --expect-original-bug \
  --qemu /absolute/path/to/preserved-old-qemu-system-aarch64 \
  --output /absolute/private/path/new-negative-control
```

The negative control requires the old bad reply, readback of EDK2-style
writes at the wrong CPU address, and an unchanged black QMP image. It then
uses the independent Linux-style decode to prove that the display backend
can render those same RGB pixels at the correct CPU address. This control
passed on old QEMU SHA-256
`9fa618ecfa9a630c659acc8123a463bfc8fa99f16c26c2373b203b69721d931b`.
The default test must fail on that old binary.

The result JSON labels negative-control reproduction separately from a fixed
test pass. A mailbox/display regression establishes this address contract,
not a guest OS boot, DSI/VideoCore graphics, an interactive desktop or Pi 5
hardware fidelity. The actual unchanged-firmware run and Linux/FreeBSD boot
controls remain necessary after integrating the patch.
