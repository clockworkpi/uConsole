# Precautions
The following-mentioned flashing operation requires your familiarity with Linux and firmware flashing.   
**Please do not attempt to try it without sufficient experience!**  
**Please do not attempt to try it without sufficient experience!**  
**Please do not attempt to try it without sufficient experience!**  

as we will not be held responsible for any consequences. Thank you for your understanding.

There are risks involved in flashing the 4g ext, and it is necessary for you to have a certain understanding of these background information. During the testing process, it is also not possible for me to test whether all SIM cards worldwide can easily connect to the internet.

# Purpose

To downgrade from 9011 to 9001.  

# Power On 4G ext

we assume that you already know how to power on the 4G ext by https://github.com/clockworkpi/uConsole/wiki/How-to-use-the-4G-extension

if not ,these steps are not safe for your device

# Checking usb id
```
sudo lsusb

Bus 001 Device 009: ID 1e0e:9011 Qualcomm / Option SimTech, Incorporated
```

OR AT COMMAND

```
echo -en "AT+CUSBPIDSWITCH?\r\n" | sudo socat - /dev/ttyUSB2,crnl
```

we expecting response contains **9011**, not 9001

# Flashing part

if you got **9011** from

```
echo -en "AT+CUSBPIDSWITCH?\r\n" | sudo socat - /dev/ttyUSB2,crnl
```

then you can try to downgrade firmware

**All processess  done on uConsole CM4 or A06**


## Process Summary:

Download whole package from 

https://github.com/clockworkpi/uConsole/raw/master/Bin/4G/LE20B06SIM7600G22_cpi_arm64.tar.gz  

and uncompress it of course

```
tar zxvf LE20B06SIM7600G22_cpi_arm64.tar.gz
cd LE20B06SIM7600G22_cpi_arm64
```

First, send the command `AT+BOOTLDR` to **/dev/ttyUSB3** to put the device into bootloader mode. At this point, the LED on the back will not light up.

```
echo -en  "AT+BOOTLDR\r\n" |sudo socat - /dev/ttyUSB3,crnl
```

Next, use `sudo ./fastboot/bin/fastboot devices` to ensure that the device is detected. It should display something like `MDM9607 fastboot`.   
If there is no device information, the operation must be halted.

Then, use `./flash.sh` from the compressed package to flash the firmware, which should take about **40** seconds.

here is the output of flash.sh

```
cpi@raspberrypi:~/LE20B04SIM7600G22_cpi $ ./flash.sh 
sending 'aboot' (447 KB)...
OKAY [  0.018s]
writing 'aboot'...
OKAY [  0.563s]
finished. total time: 0.583s
sending 'rpm' (156 KB)...
OKAY [  0.008s]
writing 'rpm'...
OKAY [  0.064s]
finished. total time: 0.072s
sending 'sbl' (210 KB)...
OKAY [  0.010s]
writing 'sbl'...
OKAY [  0.089s]
finished. total time: 0.099s
sending 'tz' (514 KB)...
OKAY [  0.020s]
writing 'tz'...
OKAY [  0.175s]
finished. total time: 0.195s
sending 'modem' (36096 KB)...
OKAY [  1.143s]
writing 'modem'...
OKAY [ 11.450s]
finished. total time: 12.593s
sending 'boot' (5536 KB)...
OKAY [  0.179s]
writing 'boot'...
OKAY [  1.744s]
finished. total time: 1.925s
sending 'system' (51072 KB)...
OKAY [  1.616s]
writing 'system'...
OKAY [ 17.308s]
finished. total time: 18.924s
sending 'recovery' (6010 KB)...
OKAY [  0.193s]
writing 'recovery'...
OKAY [  1.893s]
finished. total time: 2.086s
sending 'recoveryfs' (10240 KB)...
OKAY [  0.328s]
writing 'recoveryfs'...
OKAY [  3.229s]
finished. total time: 3.558s
rebooting...

finished. total time: 0.005s
```

Once the flashing is completed, the device will automatically reboot, and the LED on the back will light up again.

At this point, run `sudo dmesg |tail` to check for the following output:
```
[ 1033.836166] option 1-1.3:1.2: GSM modem (1-port) converter detected
[ 1033.836636] usb 1-1.3: GSM modem (1-port) converter now attached to ttyUSB0
[ 1033.837179] option 1-1.3:1.3: GSM modem (1-port) converter detected
[ 1033.837597] usb 1-1.3: GSM modem (1-port) converter now attached to ttyUSB1
[ 1033.838125] option 1-1.3:1.4: GSM modem (1-port) converter detected
[ 1033.838575] usb 1-1.3: GSM modem (1-port) converter now attached to ttyUSB2
[ 1033.839506] option 1-1.3:1.5: GSM modem (1-port) converter detected
[ 1033.840027] usb 1-1.3: GSM modem (1-port) converter now attached to ttyUSB3
[ 1033.840560] option 1-1.3:1.6: GSM modem (1-port) converter detected
[ 1033.840949] usb 1-1.3: GSM modem (1-port) converter now attached to ttyUSB4
```

If you see this output, it means that the firmware has been successfully flashed and the module is not bricked.  

```
cpi@raspberrypi:~/LE20B06SIM7600G22_cpi_arm64 $ sudo lsusb
Bus 001 Device 008: ID 1e0e:9001 Qualcomm / Option SimTech, Incorporated
```

# Useful AT commands

* check firmware version
```
echo -en "AT+GMR\r\n" | sudo socat - /dev/ttyUSB2,crnl
+GMR: LE20B06SIM7600G22
OK

```

* check sim card status
```
echo -en  "AT+cpin?\r\n" |sudo socat - /dev/ttyUSB2,crnl
+CPIN: READY
OK
```
* check signal quality
```
echo -en  "AT+CSQ\r\n" |sudo socat - /dev/ttyUSB2,crnl
+CSQ: 20,99

OK
```

* get imei 
```
echo -en  "AT+CGSN\r\n" |sudo socat - /dev/ttyUSB2,crnl

86263605126xxxx

OK
```
```
echo -en  "AT+CIMI\r\n" |sudo socat - /dev/ttyUSB2,crnl
46001341620xxxx

OK
```

