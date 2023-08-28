# uConsole


## uConsole OS Images

### CM4 
* http://dl.clockworkpi.com/uConsole_CM4_v1.3g_64bit.img.7z   
md5sum 9dbd07a50967f4015ad9c13795cd71c6
* http://dl.clockworkpi.com/uConsole_CM4_v0.1b_xfce_64bit.img.7z  (based on [RPI-lite](https://downloads.raspberrypi.org/raspios_lite_armhf/images/raspios_lite_armhf-2023-05-03/2023-05-03-raspios-bullseye-armhf-lite.img.xz) with xfce)   
md5sum a191603d7da0f826d347f1bb8d525687  
 This version is optimized for immersive writing and comes pre-installed with [Obsidian](https://obsidian.md/).


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

* [A06 core mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_DevTerm_A06_Core_for_Mainboard_V3.14_Schematic.pdf)
* [R01 core mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_DevTerm_R01_Core_for_Mainboard_V3.14_Schematic.pdf)
* [Mainboard v3.14 schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Mainboard_V3.14_Schematic.pdf)
* [CM4 adapter schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_Adapter_CM4_Schematic.pdf)
* [4G expansion Schematic](https://github.com/clockworkpi/uConsole/blob/master/clockwork_UC_4G_Schematic.pdf)


## Gearbox 

### A06 
Gearbox is a script tool used to adjust the big.LITTLE architecture of the A06 chip.  
you can get it by running 
```
sudo apt update 
sudo apt install -y devterm-gearbox-a06
```

In latest os image of A06, default Gearbox is set to use 4 LITTLE core with 816Mhz,GPU at 400Mhz

you can run `a06-gearbox` to see the current core status.

```
Current Status:
+-----------------------------------+-----------------+-----------+
|            Cortex-A53             |   Cortex-A72    | Mali-T860 |
+--------+--------+--------+--------+--------+--------+-----------+
| CPU 0  | CPU 1  | CPU 2  | CPU 3  | CPU 4  | CPU 5  |    GPU    |
+--------+--------+--------+--------+--------+--------+-----------+
| 816Mhz | 816Mhz | 816Mhz | 816Mhz |  OFF   |  OFF   |   400MHz  |
+--------+--------+--------+--------+--------+--------+-----------+
CPU Governor: schedutil    GPU Governor: simple_ondemand
```

Run `sudo a06-gearbox -s [GEAR]` to set gear,GEAR would be 1,2,3,4,5,6

There are 6 gears in gearbox

```
               1 for simple writing tasks with long battery life.
               2 for browsing most websites with long battery life.
               3 for most 2D games and emulators.
               4 for playing videos and 3D games.
               5 for performance-first tasks.
               6 for max performance, max power (usage).

```



## Community
Please visit our [Github Wiki](https://github.com/clockworkpi/uConsole/wiki) and https://forum.clockworkpi.com for more information.


