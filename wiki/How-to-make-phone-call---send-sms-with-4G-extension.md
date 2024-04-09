First we need [power on the 4G extension](https://github.com/clockworkpi/uConsole/wiki/How-to-use-the-4G-extension) on the uConsole

Then use [AT commands](https://github.com/clockworkpi/uConsole/blob/master/SIM7500_SIM7600%20Series_AT%20Command%20Manual_V3.00.pdf) to do the jobs in uConsole

We use /dev/ttyUSB3 as AT port  

## Check sim card if is ready
```
echo -en "AT+CPIN?\r\n" | sudo socat - /dev/ttyUSB3,crnl

+CPIN: READY

OK
```

## Check ISP info
```
echo -en "AT+COPS?\r\n" | sudo socat - /dev/ttyUSB3,crnl

+COPS: 0,0,"SOME-ISP",7

OK
```

## Make phone call
```
echo -en "ATD123456;\r\n" | sudo socat - /dev/ttyUSB3,crnl

OK

+CLCC: 3,0,2,0,0,"123456",129
```

replace **123456** with your really target phone number

## Hang up phone call
```
echo -en "AT+CVHU=0\r\n" | sudo socat - /dev/ttyUSB3,crnl
echo -en "ATH\r\n" | sudo socat - /dev/ttyUSB3,crnl

OK

+CLCC: 3,0,6,0,0,"123456",129

OK
```

## Answer phone call
```
echo -en "ATA\r\n" | sudo socat - /dev/ttyUSB3,crnl

VOICE CALL: BEGIN
or
NO CARRIER

```
## Network state
```
echo -en "AT+CGREG?\r\n" | sudo socat - /dev/ttyUSB3,crnl

+CGREG: 2,1,E7B6,EA22BB2

OK
```

## Send text message
```
echo -e "ATZ\r" | sudo socat - /dev/ttyUSB3,crnl
sleep 1
echo -e "AT+CMGF=1\r" | sudo socat - /dev/ttyUSB3,crnl
sleep 1
echo -e "AT+CMGS=\"123456\""  | sudo socat - /dev/ttyUSB3,crnl
sleep 1
echo -e "Hello this is SMS message from shell\x1A" | sudo socat - /dev/ttyUSB3,crnl
sleep 1
echo -en "ATE0\r\n" | sudo socat - /dev/ttyUSB3,crnl
```
replace **123456** with your really target phone number



***

For more AT commands, please refer the [manual](https://github.com/clockworkpi/uConsole/blob/master/SIM7500_SIM7600%20Series_AT%20Command%20Manual_V3.00.pdf)