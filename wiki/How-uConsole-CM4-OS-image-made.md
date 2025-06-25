# How uConsole CM4 OS Image Is Made

This guide explains how to create an OS image for the uConsole CM4 module. Since the uConsole and DevTerm share the same mainboard, the process involves adapting the DevTerm CM4 OS image with updates specific to the uConsole, such as a new kernel for its LCD screen.

---

## **Base OS Image**

The base OS image used is the DevTerm CM4 image:

- Download the base image:  
  [DevTerm_CM4_v0.1a_64bit.img.bz2](http://dl.clockworkpi.com/DevTerm_CM4_v0.1a_64bit.img.bz2)  
  md5sum: 4d990ec702d0b6383f6e43bbeb3e3274  

For detailed instructions on creating the base image from scratch, refer to:  
[Create DevTerm CM4 OS Image from Scratch](https://github.com/clockworkpi/DevTerm/wiki/Create-DevTerm-CM4-OS-image-from-scratch).

---

## **Kernel Update**

To update the kernel for the uConsole CM4:

1. Use the kernel files located here:  
   [uConsole Kernel for CM4](https://github.com/clockworkpi/uConsole/tree/master/Code/patch/cm4/20230630)

2. Create a `.deb` package for the kernel:  
   `uconsole_kernel_cm4-rpi` is built from the compiled output files.

3. Ensure the correct configuration in the `config.txt` file. Below is an example configuration:

   ```text
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

   **Key line:**  
   `dtoverlay=devterm-panel-uc` (This sets the panel configuration specific to the uConsole.)

---

## **Chroot Environment**

To modify the image in a chroot environment:

1. Mount the image:

   ```bash
   sudo mkdir -p /mnt/p1
   sudo losetup --show -f -P DevTerm_CM4_v0.1a_64bit.img # Assume loop0
   sudo mount /dev/loop0p2 /mnt/p1
   sudo mount /dev/loop0p1 /mnt/p1/boot
   ```

2. Set up the chroot environment:

   ```bash
   cd /mnt/p1
   sudo mount --bind /dev dev/
   sudo mount --bind /sys sys/
   sudo mount --bind /proc proc/
   sudo mount --bind /dev/pts dev/pts
   sudo chroot .
   ```

3. Inside the chroot environment:

   - Update the system and kernel:

     ```bash
     sudo apt update
     sudo apt install -y uconsole-kernel-cm4-rpi
     ```

   - Install additional software (games, emulators, and utilities):

     ```bash
     sudo apt install -y devterm-tic80-cpi uconsole-love2d retroarch dosbox \
     devterm-cavestory-cpi-cm4 uconsole-liko12 uconsole-lowresnx \
     uconsole-dosbox-staging pppoe uconsole-4g-util-cm4
     ```

   - Configure the 4G extension module (optional):

     ```bash
     sudo bash -c 'cat << EOF > /etc/modprobe.d/blacklist-qmi.conf
     blacklist qmi_wwan
     blacklist cdc_wdm
     EOF'
     ```

   - Remove unnecessary software (e.g., thermal printer utilities, as uConsole does not include a thermal printer):

     ```bash
     sudo apt remove devterm-thermal-printer devterm-thermal-printer-cm4
     ```

   - Perform pre-configurations:

     ```bash
     cd /usr/local/bin/
     git clone -b uconsole-cm4 https://github.com/cuu/skel.git
     cp -rf skel/etc/xdg/autostart/first-touch.desktop /etc/xdg/autostart/
     cp -rf skel/usr/local/bin/chgpas.sh /usr/local/bin/
     ```

   - Clean up old screen rotation settings (the new kernel already handles screen rotation):

     ```bash
     nano /boot/cmdline.txt  # Remove "fbcon=rotate:1"
     rm -rf /etc/X11/Xsession.d/90custom_xrandr
     rm -rf /etc/lightdm/setup.sh
     nano /etc/lightdm/lightdm.conf  # Comment out or delete the line:
     # greeter-setup-script=/etc/lightdm/setup.sh
     ```

4. Exit the chroot environment:

   ```bash
   exit
   ```

---

## **Clean Chroot Environment**

After finishing work in the chroot environment, clean up:

```bash
cd -
sudo umount /mnt/p1/dev/pts
sudo umount /mnt/p1/dev
sudo umount /mnt/p1/proc
sudo umount /mnt/p1/sys

# Clear bash history
sudo rm -rf /mnt/p1/root/.bash_history

sudo umount /mnt/p1/boot
sudo umount /mnt/p1
sudo losetup -D /dev/loop0
```

---

## **Notes**

- All official uConsole OS images can be found here:  
  [uConsole Images Repository](https://github.com/clockworkpi/uConsole/tree/master/images)




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




- Happy hacking!
