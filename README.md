# uConsole


## uConsole OS Images

### CM4 
* http://dl.clockworkpi.com/uConsole_CM4_v1.3g_64bit.img.7z   
md5sum 9dbd07a50967f4015ad9c13795cd71c6
  
### A06 
* http://dl.clockworkpi.com/uConsole_A06_v1.1e.img.7z  
md5sum 56bbb623f41bf6327d408fb415052819 
  
### R01
* http://dl.clockworkpi.com/uConsole_R01_v1.3b.img.7z  
md5sum 53ca37ccc0333436d06fb5978ac699fd  


After downloading the files, you will need to extract or decompress them. Please keep in mind that MacOS 11.6 or a higher version is required to extract 7z files.  
  
To flash the OS image, you can use the following tools:  
  
- For Windows and macOS users, [Etcher](https://etcher.balena.io/) can be used to flash the image.  
- Linux users can employ the "dd" command to flash the image.  


If you want to use the 4G extension, you can find helpful tips on how to use it on the [uConsole Wiki](https://github.com/clockworkpi/uConsole/wiki/How-to-use-the-4G-extension).

## uConsole Keyboard Firmware
uConsole keyboard firmware flash program available. You can download it from this link: [uConsole Keyboard Firmware Flash Program](https://github.com/clockworkpi/uConsole/raw/master/Bin/uconsole_keyboard_flash.tar.gz).

Here's how you can flash the firmware on uConsole(A06 or CM4) or a PC running Ubuntu 22.04:

1. Download the uconsole_keyboard_flash.tar.gz file.
2. Extract the contents of the archive: `tar zxvf uconsole_keyboard_flash.tar.gz`.
3. Install the required package using the following command: `sudo apt install -y dfu-util`.
4. Navigate to the extracted directory: `cd uconsole_keyboard_flash`.
5. Execute the flash script with root privileges: `sudo ./flash.sh`.
6. If everything goes well, you will see a progress bar indicating the flashing process.
7. If any issues occur or the keyboard loses control (which is unlikely), simply reboot uConsole to resolve it.
8. Rest assured that this flash program will not brick your keyboard.

## 4G extension firmware

[Upgrade 4G extension firmware](https://github.com/clockworkpi/uConsole/wiki/How-to-upgrade-4G-extension-firmware)  


## Assembly Guidelines

* [Assembly Guidelines](https://github.com/clockworkpi/uConsole/blob/master/Clockwork_uConsole_Assembly_Guidelines.pdf)  

## Schematic

* [CM4 adapter schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Adapter_CM4_Schematic.pdf)
* [4G expansion Schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_UC_4G_Schematic.pdf)


## Community
Please visit our [Github Wiki](https://github.com/clockworkpi/uConsole/wiki) and https://forum.clockworkpi.com for more information.


