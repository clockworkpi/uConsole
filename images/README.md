# uConsole OS Image

## uConsole

* CM4 http://dl.clockworkpi.com/uConsole_CM4_v1.3g_64bit.img.7z   
  md5sum 9dbd07a50967f4015ad9c13795cd71c6
  
* A06 http://dl.clockworkpi.com/uConsole_A06_v1.1e.img.7z  
  md5sum 56bbb623f41bf6327d408fb415052819 
  
* R01 http://dl.clockworkpi.com/uConsole_R01_v1.3b.img.7z  
  md5sum 53ca37ccc0333436d06fb5978ac699fd  


After downloading the files, you need to uncompress them. Please note that MacOS requires version 11.6 or higher to uncompress 7z files.

For flashing the uConsole OS image, you can use the following tools:

* Windows and macOS users can use [Etcher](https://etcher.balena.io/) to flash the image.
* Linux users can use the "dd" command to flash the image.

If you want to use the 4G extension, you can find helpful tips on how to use it on the [uConsole Wiki](https://github.com/clockworkpi/uConsole/wiki/How-to-use-the-4G-extension).

uConsole keyboard firmware flash program available. You can download it from this link: [uConsole Keyboard Firmware Flash Program](https://github.com/clockworkpi/uConsole/raw/master/Bin/uconsole_keyboard_flash.tar.gz).

Here's how you can flash the firmware on uConsole or a PC running Ubuntu 22.04:

1. Download the uconsole_keyboard_flash.tar.gz file.
2. Extract the contents of the archive: `tar zxvf uconsole_keyboard_flash.tar.gz`.
3. Install the required package using the following command: `sudo apt install -y dfu-util`.
4. Navigate to the extracted directory: `cd uconsole_keyboard_flash`.
5. Execute the flash script with root privileges: `sudo ./flash.sh`.
6. If everything goes well, you will see a progress bar indicating the flashing process.
7. If any issues occur or the keyboard loses control (which is unlikely), simply reboot uConsole to resolve it.
8. Rest assured that this flash program will not brick your keyboard.

