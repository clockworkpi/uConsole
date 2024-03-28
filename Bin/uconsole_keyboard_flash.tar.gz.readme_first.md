# Readme

This tar.gz contains a auto flash tool for quick flashing Arduino IDE compiled bin file to the uConsole keyboard while the keyboard is plugged in the uConsole and uConsole is running some linux system.

***DO NOT*** confused the bin file in the tar.gz  with other uconsole.kbd.***.bin files, they are not the same thing.

So do not use uconsole.kbd.***.bin  with this flashing tool.


Here's how you can flash the firmware on uConsole(A06 or CM4) or a PC running Ubuntu 22.04:

1. Download the uconsole_keyboard_flash.tar.gz file.
2. Extract the contents of the archive: `tar zxvf uconsole_keyboard_flash.tar.gz`.
3. Install the required package using the following command: `sudo apt install -y dfu-util`.
4. Navigate to the extracted directory: `cd uconsole_keyboard_flash`.
5. Execute the flash script with root privileges: `sudo ./flash.sh`.
6. If everything goes well, you will see a progress bar indicating the flashing process.
7. If any issues occur or the keyboard loses control (which is unlikely), simply reboot uConsole to resolve it.
8. Rest assured that this flash program will not brick your keyboard.


