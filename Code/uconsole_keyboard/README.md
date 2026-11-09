## Command-line build

From the repository root, run `make firmware` with Arduino CLI on PATH and
the legacy `stm32duino:STM32F1` core installed. The resulting file is
`build/firmware/uconsole_keyboard.ino.bin`. This target only compiles; it
does not flash a device. `make flash-tool` separately builds the native
serial reset helper into `build/upload-reset.elf` without overwriting the
bundled binaries.

The explicit board settings are Generic STM32F103R, STM32F103RB (20 KiB RAM,
128 KiB flash), STM32duino bootloader, 48 MHz, smallest optimization:

```sh
arduino-cli compile \
  --fqbn stm32duino:STM32F1:genericSTM32F103R:device_variant=STM32F103RB,upload_method=DFUUploadMethod,cpu_speed=speed_48mhz,opt=osstd \
  --output-dir build/firmware Code/uconsole_keyboard
```

Check the MCU fitted to your keyboard before flashing; override `FQBN` for a
different variant. The DFU selection links the application at `0x08002000`,
after the existing bootloader. Compilation alone does not verify operation
on the keyboard.

### Linux ARM64 host

The documented 2021.2.22 package does not provide an ARM64 `stm32tools`
bundle, so Boards Manager installation fails on this host architecture.
For compilation, manually extract the
[2021.2.22 core archive](https://dan.drown.org/stm32duino/STM32F1-2021.2.22.zip)
to `~/Arduino/hardware/stm32duino/STM32F1`, and extract Arduino's
[ARM64 GCC 7-2018-q2 toolchain](https://downloads.arduino.cc/tools/gcc-arm-none-eabi-7-2018-q2-update-linuxarm64.tar.bz2)
to a persistent location. Check SHA-256 before extracting:

```text
STM32F1-2021.2.22.zip:
e0f489d3ee10ffce45a826396249add5fc4a3e6699c780dab70a3836586a7917
gcc-arm-none-eabi-7-2018-q2-update-linuxarm64.tar.bz2:
6fb5752fb4d11012bd0a1ceb93a19d0641ff7cf29d289b3e6b86b99768e66f76
```

Create `platform.local.txt` alongside that core's `platform.txt`, containing
`compiler.path=/absolute/path/to/compiler/bin/` (including the trailing slash).
The core archive includes the required USBComposite library. Arduino CLI
may display its internal platform version as `0.1.2`; the archive version
is `2021.2.22`. This supplies the compilation dependencies, not the missing
ARM64 upload tools. Use the repository's separate flashing instructions
when ready to test hardware.

Verified on Linux ARM64 with Arduino CLI 1.5.2-rc.1 and GCC 7.2.1: 33,696
bytes flash, 4,680 bytes RAM. Hardware execution remains unverified.

## Original Arduino IDE setup

Arduino 1.8.13

http://dan.drown.org/stm32duino/package_STM32duino_index.json

STM32F1xx/GD32F1xx boards
by stm32duino version 2021.2.22

  GENERIC STM32F103R series

  gd32f1_generic_boot20_pc13.bin
  generic_boot20_pc13.bin

---

## How to Compile Keyboard Firmware

**Disclaimer**: The instruction below was not prepared by ClockworkPi. Follow it at your own risk and make sure you know what you are doing.

You can easily modify, compile, and upload the keyboard firmware using the Arduino IDE.

1. Download and install the Arduino IDE: https://www.arduino.cc/en/software/
2. Run the Arduino IDE, go to `Menu > File > Preferences`. At the bottom of the first tab, there is an "invisible" input field called **Additional Boards Manager URLs**.
3. Enter the following into this field: `http://dan.drown.org/stm32duino/package_STM32duino_index.json`, then save.
4. Go to `Menu > Tools > Board > Boards Manager` _(or press `<ctrl> + <shift> + b`)_
5. Search for the newly added board `STM32F1xx/GD32F1xx` and install it.
6. Clone the repository `https://github.com/clockworkpi/uConsole.git` somewhere _(or otherwise download the keyboard firmware source code)_.
7. In the Arduino IDE, open the file `/uConsole/Code/uconsole_keyboard/uconsole_keyboard.ino`. It will open many tabs in a new window.
8. Select the board: `Menu > Tools > Board > STM32F1xx/GD32F1xx boards > GENERIC STM32F103R series`
9. Compile the firmware using `Menu > Sketch > Verify/Compile` _(`<ctrl> + r`)_
10. If it didn’t fail — you are almost there.
11. Now export the compiled binary using `Menu > Sketch > Export Compiled Binary` _(`<alt> + <shift> + s`)_
12. The binary file will appear near your source code. Check the directory `/uConsole/Code/uconsole_keyboard/build/stm32duino.STM32F1.genericSTM32F103R/`
13. You should find the file `uconsole_keyboard.ino.bin`
14. Great! Now you can upload the compiled firmware to the keyboard. Follow the official instructions: https://github.com/clockworkpi/uConsole/tree/master?tab=readme-ov-file#uconsole-keyboard-firmware
15. Download the “uConsole Keyboard Firmware Flash Program” to the uConsole, unpack it, and replace the official firmware file with your own.

**Hint:** It is highly recommended to configure SSH access to your uConsole. In case something goes wrong, you will be able to recover the firmware without needing to connect a USB keyboard to the uConsole.
