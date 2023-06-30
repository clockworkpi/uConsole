# Patch for uConsole CM4

based on `https://github.com/raspberrypi/linux.git`

commit hash: 3a33f11c48572b9dd0fecac164b3990fc9234da8

## Prepares

ubuntu 22.04 with aarch64-linux-gnu-

```
sudo apt install -y build-essential gcc-aarch64-linux-gnu 
```

## Example process of compiling

```
cd
git clone https://github.com/raspberrypi/linux.git

cd linux

wget https://raw.githubusercontent.com/clockworkpi/uConsole/master/Code/patch/cm4/20230630/0001-patch-cm4.patch

git reset --hard 3a33f11c48572b9dd0fecac164b3990fc9234da8

git apply 0001-patch-cm4.patch


KERNEL=kernel8 make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- bcm2711_defconfig
KERNEL=kernel8 make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j2

rm -rf ./modules/*

INSTALL_MOD_PATH=./modules make modules_install


rm modules/lib/modules/*/build
rm modules/lib/modules/*/source

```

## config.txt 

In order to use compiled kernel8.img with CM4 in uConsole ,we have to setup a config.txt

```
disable_overscan=1
dtparam=audio=on
[pi4]
max_framebuffers=2

[all]
ignore_lcd=1
dtoverlay=dwc2,dr_mode=host
dtoverlay=vc4-kms-v3d-pi4,cma-384
dtoverlay=devterm-pmu
dtoverlay=devterm-panel-uc
dtoverlay=devterm-misc
dtoverlay=audremap,pins_12_13

dtparam=spi=on
gpio=10=ip,np
```


