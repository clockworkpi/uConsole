Based on 4d990ec702d0b6383f6e43bbeb3e3274 http://dl.clockworkpi.com/DevTerm_CM4_v0.1a_64bit.img.bz2

Since the uConsole and devterm are using the same main board 

Just need to update a new kernel for the uConsole LCD screen

original create os image from scratch is here:

https://github.com/clockworkpi/DevTerm/wiki/Create-DevTerm-CM4-OS-image-from-scratch

## Kernel

https://github.com/clockworkpi/uConsole/tree/master/Code/patch/cm4/20230630

Create a deb file of uconsole_kernel_cm4-rpi based on kernel compiled output files 

Another important thing to note is the correct configuration file, "config.txt".

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
especially the line **dtoverlay=devterm-panel-uc**

## Chroot
Chroot into DevTerm_CM4_v0.1a_64bit.img
by
```
sudo mkdir -p /mnt/p1
sudo losetup --show -f -P  DevTerm_CM4_v0.1a_64bit.img #assume loop0
sudo mount /dev/loop0p2 /mnt/p1
sudo mount /dev/loop0p1 /mnt/p1/boot

cd /mnt/p1
sudo mount --bind /dev dev/
sudo mount --bind /sys sys/
sudo mount --bind /proc proc/
sudo mount --bind /dev/pts dev/pts
#sudo mv etc/ld.so.preload etc/ld_so_preload
sudo chroot .
```

## In chroot

```
sudo apt update
#update kernel
sudo apt install -y uconsole_kernel_cm4-rpi 
#install additional games/emulators
sudo apt install -y devterm-tic80-cpi uconsole-love2d retroarch dosbox  devterm-cavestory-cpi-cm4  uconsole-liko12 uconsole-lowresnx uconsole-dosbox-staging pppoe uconsole-4g-util-cm4

#for 4G extension
sudo bash -c 'cat << EOF > /etc/modprobe.d/blacklist-qmi.conf
blacklist qmi_wwan
blacklist cdc_wdm
EOF'

# uconsole does not have thermal printer
sudo apt remove devterm-thermal-printer devterm-thermal-printer-cm4 # uconsole does not have thermal printer

#pre configs
cd /usr/local/bin/
git clone -b  uconsole-cm4 https://github.com/cuu/skel.git
cp -rf skel/etc/xdg/autostart/first-touch.desktop /etc/xdg/autostart/
cp -rf skel/usr/local/bin/chgpas.sh /usr/local/bin/

#remove old configs for screen rotation,since new uconsole cm4 kernel did screen rotation 

nano /boot/cmdline.txt ,remove   "fbcon=rotate:1"  
rm -rf /etc/X11/Xsession.d/90custom_xrandr  
rm -rf /etc/lightdm/setup.sh  
edit /etc/lightdm/lightdm.conf  
comment out or delete line :  
 "greeter-setup-script=/etc/lightdm/setup.sh"  

exit #quit chroot
```

## Clean chroot 

do it everytime when you are done with chroot enviroment  

```
cd -
sudo umount /mnt/p1/dev/pts
sudo umount /mnt/p1/dev
sudo umount /mnt/p1/proc
sudo umount /mnt/p1/sys
##clear bash 
sudo rm -rf /mnt/p1/root/.bash_history
sudo umount /mnt/p1/boot
sudo umount /mnt/p1
sudo losetup -D /dev/loop0
```
All the uConsole image can be found at : https://github.com/clockworkpi/uConsole/tree/master/images

That's it , happy hacking 