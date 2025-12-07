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
