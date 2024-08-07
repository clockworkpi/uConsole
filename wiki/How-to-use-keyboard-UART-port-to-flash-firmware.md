On uConsole keyboard back,there is a **UART** port which can be used with `stm32flash` to flash keyboard firmware  

Especially at when your keyboard met problem  

here is the process:  

* put the switch 1 ON
* connect uart with a **fpc 6p connector 0.5mm** to a serial-usb converter
* use stm32flash to flash bin file

## Whole connection view
![uconsole_kbd_stm32flash](https://github.com/user-attachments/assets/66a0028a-b6c1-4af2-92e7-91c6179fe272)

## UART ports 
![sch](https://github.com/user-attachments/assets/06d88726-af96-4573-ba60-74b870bb07cf)

3 is TX(PA9)   
4 is RX(PA10)  


## 1st pin of UART on board
![7130803](https://github.com/user-attachments/assets/eeed0c0a-ea86-4496-b931-dcc074835eca)

## Flashing command

```
$ sudo stm32flash -w uconsole.kbd.0.4_48mhz.bin -v -S 0x08000000 /dev/ttyUSB0
```
## Normal flashing output
```
stm32flash 0.5

http://stm32flash.sourceforge.net/

Using Parser : Raw BINARY
Interface serial_posix: 57600 8E1
Version      : 0x22
Option 1     : 0x00
Option 2     : 0x00
Device ID    : 0x0410 (STM32F10xxx Medium-density)
- RAM        : Up to 20KiB  (512b reserved by bootloader)
- Flash      : Up to 128KiB (size first sector: 4x1024)
- Option RAM : 16b
- System RAM : 2KiB
Write to memory
Erasing memory
Wrote and verified address 0x08010000 (100.00%) Done.
```

