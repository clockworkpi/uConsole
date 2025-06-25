# How uConsole CM4 OS Image Is Made

This guide explains how to create an OS image for the uConsole CM4 module.

---

# uConsole_CM4_v2.0_64bit.img.7z
Since v2.0, cm4 os image is made by [pi-gen](https://github.com/cuu/pi-gen/tree/uconsole_arm64)

just clone [pi-gen](https://github.com/cuu/pi-gen/tree/uconsole_arm64) repo, branch uconsole_arm64  

run `sudo ./build.sh` in ubuntu linux PC  


- kernel source: https://github.com/cuu/ClockworkPi-linux/commit/9a1e3adc9d1431889f62e633ae791bfc9a6cf535
here is kernel compiling commands:
```
export KERNEL=kernel8
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-

make bcm2711_defconfig

make -j4

mkdir -p ../modules
rm -rf ../modules/*

INSTALL_MOD_PATH=../modules make modules_install

mkdir -p ../firmware
rm -rf ../firmware/*
mkdir -p ../firmware/overlays

cp arch/arm64/boot/Image ../firmware/$KERNEL.img
cp arch/arm64/boot/dts/broadcom/*.dtb ../firmware
cp arch/arm64/boot/dts/overlays/*.dtb* ../firmware/overlays/
cp arch/arm64/boot/dts/overlays/README ../firmware/overlays/

KERNEL_VER=$(make kernelrelease)
cp arch/arm64/boot/Image.gz ../vmlinuz-${KERNEL_VER}

cp System.map  ../System.map-${KERNEL_VER}
cp .config  ../config-${KERNEL_VER}

mkdir -p ../linux-headers-${KERNEL_VER}

make headers_install INSTALL_HDR_PATH=../linux-headers-${KERNEL_VER}
```


- All official uConsole OS images can be found here:  
  [uConsole Images Repository](https://github.com/clockworkpi/uConsole/tree/master/images)


- Happy hacking!
