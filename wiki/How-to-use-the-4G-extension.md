4G extension on the uConsole is power down by default

## First we need to power on the 4G extension

### CM4 

run `enable_4g_cm4.sh` or `uconsole-4g-cm4 enable`, depends on the version of your os image, both can do the same job

Please wait for it to complete, which may take about 20 seconds. Please be patient and try not press ctrl+c.  

next is **VERY IMPORTANT**, to check the version of your 4G extension by AT commands:

```
echo -en "AT+CUSBPIDSWITCH?\r\n" | sudo socat - /dev/ttyUSB2,crnl
```

we expecting to see **9001**

if you are other module number ,eg :9011

you don't need any following steps

just make sure the SIM card inserted correctlly,and use **sudo ifconfig** to see if there is a **usb0** 

if there is a `usb0`,then all is set  

normally you will have ipv4 and ipv6 address,now you can use 4G card to view internet  

#### 9001 part

use `mmcli -L` to see if there is a modem like 

```
/org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] SIMCOM_SIM7600G-H
```
if not ,try 
```
sudo systemctl restart ModemManager
``` 
then  
use `mmcli -L` to check again, some modems will require ModemManager to restart to see the device 

If unpack your back shell , you also will see a green LED light is flashing on 4G ext once got power on

### A06 and R01

run `enable_4g.sh` or `uconsole-4g enable` depends on the version of your os image, both can do the same job

Please wait for it to complete, which may take about 20 seconds. Please be patient and try not press ctrl+c.  

next is **VERY IMPORTANT**, to check the version of your 4G extension by AT commands:

```
echo -en "AT+CUSBPIDSWITCH?\r\n" | sudo socat - /dev/ttyUSB2,crnl
```

we expecting to see **9001**

if you are other module number ,eg :`9011`

**you don't need any following steps**

just make sure the SIM card inserted correctlly,and use **sudo ifconfig** to see if there is a **usb0** 

if there is a usb0,then all is set  

normally you will have ipv4 and ipv6 address,now you can use 4G card to view internet

#### 9001 part
use `mmcli -L` to see if there is a modem like 

```
/org/freedesktop/ModemManager1/Modem/0 [QUALCOMM INCORPORATED] SIMCOM_SIM7600G-H
```
and if unpack your back shell , you will see a green LED light is flashing on 4G ext

## Connect to 4G network

for this purpose,we need `nmcli` to create a gsm connection first

### find out the primary port

```
mmcli -m any | grep "primary port"

```
you will get like `cdc-wdm0` or  `ttyUSB2` , if you got `cdc-wdm0`, we suggest blacklisting some kernel modules by

```
sudo bash -c 'cat << EOF > /etc/modprobe.d/blacklist-qmi.conf
blacklist qmi_wwan
blacklist cdc_wdm
EOF'
```

`ttyUSB2` is what we want,but **cdc-wdm0** is still worth to try if you knew it well 
 
### Create 4g net connection and bring it up

```
sudo nmcli c add type gsm ifname ttyUSB2 con-name 4gnet apn [yourapn] gsm.username [gsmusername] gsm.password [gsmpassword]
```

```
sudo nmcli c up 4gnet
```

then if everything goes right, you will see **ppp0** in `sudo ifconfig` , which means you got an PPPoE connection and an IP address

If some errors occured like:  

```
Error: Connection activation failed: No suitable device found for this connection (device eth0 not available because profile is not compatible with device (mismatching interface name)).
```
```
Error: Connection activation failed: No suitable device found for this connection (device ttyUSB2 not available because device is not available).
```

try to use `mmcli -m  any` to see whether the SIM card is properly installed, still within its valid service period, and has data service enabled.

here is a false state example output :
```
  -----------------------------
  General  |              path: /org/freedesktop/ModemManager1/Modem/0
           |         device id: a593c5055fabfa914496a4977012f1a829cf320f
  -----------------------------
  Hardware |      manufacturer: SIMCOM INCORPORATED
           |             model: SIMCOM_SIM7600G-H
           | firmware revision: LE20B04SIM7600G22
           |         supported: gsm-umts
           |           current: gsm-umts
           |      equipment id: xxxxxxxxxxxxxxxxx
  -----------------------------
  System   |            device: /sys/devices/platform/soc/fe980000.usb/usb1/1-1/1-1.3
           |           physdev: /sys/devices/platform/soc/fe980000.usb/usb1/1-1/1-1.3
           |           drivers: option
           |            plugin: simtech
           |      primary port: ttyUSB2
           |             ports: ttyUSB0 (ignored), ttyUSB1 (gps), ttyUSB2 (at), 
           |                    ttyUSB3 (at), ttyUSB4 (audio)
  -----------------------------
  Status   |             state: failed
           |     failed reason: sim-missing
           |       power state: on
  -----------------------------
  Modes    |         supported: allowed: any; preferred: none
           |           current: allowed: any; preferred: none
  -----------------------------
  3GPP     |              imei: xxxxxxxxxxxxxxx
```

here is a connected state example output:
```
  ----------------------------------
  General  |                   path: /org/freedesktop/ModemManager1/Modem/0
           |              device id: a593c5055fabfa914496a4977012f1a829cf320f
  ----------------------------------
  Hardware |           manufacturer: SIMCOM INCORPORATED
           |                  model: SIMCOM_SIM7600G-H
           |      firmware revision: LE20B04SIM7600G22
           |              supported: gsm-umts, lte
           |                current: gsm-umts, lte
           |           equipment id: xxxxxxxxxxxxxxxxx
  ----------------------------------
  System   |                 device: /sys/devices/platform/soc/fe980000.usb/usb1/1-1/1-1.3
           |                physdev: /sys/devices/platform/soc/fe980000.usb/usb1/1-1/1-1.3
           |                drivers: option
           |                 plugin: simtech
           |           primary port: ttyUSB2
           |                  ports: ttyUSB0 (ignored), ttyUSB1 (gps), ttyUSB2 (at), 
           |                         ttyUSB3 (at), ttyUSB4 (audio)
  ----------------------------------
  Status   |         unlock retries: sim-pin (3), sim-puk (10), sim-pin2 (3), sim-puk2 (10)
           |                  state: connected
           |            power state: on
           |            access tech: lte
           |         signal quality: 74% (recent)
  ----------------------------------
  Modes    |              supported: allowed: 2g; preferred: none
           |                         allowed: 3g; preferred: none
           |                         allowed: 2g, 3g; preferred: none
           |                         allowed: 2g, 3g; preferred: 2g
           |                         allowed: 2g, 3g; preferred: 3g
           |                current: allowed: any; preferred: none
  ----------------------------------
  IP       |              supported: ipv4, ipv6, ipv4v6
  ----------------------------------
  3GPP     |                   imei: xxxxxxxxxxxxxxx
           |            operator id: 46000
           |          operator name: SOME ISP NAME
           |           registration: home
           |   packet service state: attached
  ----------------------------------
  3GPP EPS |   ue mode of operation: csps-1
           |    initial bearer path: /org/freedesktop/ModemManager1/Bearer/0
           | initial bearer ip type: ipv4
  ----------------------------------
  SIM      |       primary sim path: /org/freedesktop/ModemManager1/SIM/0
  ----------------------------------
  Bearer   |                  paths: /org/freedesktop/ModemManager1/Bearer/1
```


The connectivity of 4G may vary in different countries around the world, so please make appropriate adjustments based on the situation of your ISP operator, which may require some Linux-related skills.

## GPS 

We use AT commands to get GPS data  

### start gps
```
cpi@raspberrypi:~ $ echo -en "AT+CGPS=1\r\n" | sudo socat - /dev/ttyUSB3,crnl
OK
```

### wait about one minute depend on the gps signal,get gps data
```
cpi@raspberrypi:~ $ echo -en "AT+CGPSINFO\r\n" | sudo socat - /dev/ttyUSB3,crnl
+CGPSINFO: 3036.995500,N,12016.782856,E,200224,082124.0,40.5,0.0,
OK
```

### close gps
```
cpi@raspberrypi:~ $ echo -en "AT+CGPS=0\r\n" | sudo socat - /dev/ttyUSB3,crnl
```

make sure the **GPS antenna** is connected to the **GNSS** port on 4G extension

