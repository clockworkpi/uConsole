Based on 26f52bfde573479960d8696f407d19b9 http://dl.clockworkpi.com/DevTerm_A06_v0.2h.img.bz2

Since uconsole and devterm are using the same main board

Just need to update a new kernel for uconsole LCD screen

## Kernel

https://github.com/clockworkpi/uConsole/tree/master/Code/patch/a06/20230630

Combine linux-dtb-current-rockchip64_23.02.0-trunk_arm64.deb and linux-image-current-rockchip64_23.02.0-trunk_arm64.deb to a new deb file

One of the more complicated tasks is merging preinst, postinst, postrm, and prerm files.

```
cat  linux-image-current-rockchip64/preinst  linux-dtb-current-rockchip64/preinst > preinst

cat  linux-image-current-rockchip64/postinst  linux-dtb-current-rockchip64/postinst > postinst

cat  linux-image-current-rockchip64/postrm > postrm

cat  linux-image-current-rockchip64/prerm > prerm
```

## Expand img size
``` 
dd if=/dev/zero bs=1MiB of=DevTerm_A06_v0.2h.img conv=notrunc oflag=append count=3000
sudo losetup -f --show -P DevTerm_A06_v0.2h.img #assume got /dev/loop0
sudo gparted /dev/loop0 #resize it,fullfill empty space with last partition by mouse drag
sudo losetup -D /dev/loop0
```

## Chroot
Chroot into DevTerm_A06_v0.2h.img by  

```
sudo mkdir -p /mnt/p1
sudo losetup --show -f -P DevTerm_A06_v0.2h.img  #assume loop0
sudo mount /dev/loop0p1 /mnt/p1

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
sudo apt install -y  uconsole-kernel-current-cpi-a06


#install additional games/emulators
sudo apt install -y devterm-tic80-cpi uconsole-love2d retroarch dosbox  devterm-cavestory-cpi-cm4  uconsole-liko12 uconsole-lowresnx uconsole-dosbox-staging pppoe uconsole_4g_utils


#for 4G extension
sudo bash -c 'cat << EOF > /etc/modprobe.d/blacklist-qmi.conf
blacklist qmi_wwan
blacklist cdc_wdm
EOF'

# uconsole does not have thermal printer
sudo apt remove devterm-thermal-printer # uconsole does not have thermal printer

#pre configs
cd /usr/local/bin/
git clone -b  uconsole-a06 https://github.com/cuu/skel.git

cp -rf skel/.config /home/cpi/
cp -rf skel/.local /home/cpi
cp -rf skel/.dosbox /home/cpi
cp -rf skel/.opentyrian /home/cpi
cp -rf skel/etc/sudoers.d/cpi  /etc/sudoers.d/
chown -R cpi:cpi /home/cpi

#remove old configs for screen rotation,since new uconsole a06 kernel did screen rotation 

* nano /boot/armbianEnv.txt ,remove   "fbcon=rotate:1"  
* rm -rf /etc/X11/Xsession.d/90custom_xrandr  
* rm -rf /etc/lightdm/setup.sh  
* rm -rf /etc/lightdm/lightdm.conf.d/13-rotate-dsi.conf  

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
sudo umount /mnt/p1
sudo losetup -D /dev/loop0
```
All uconsole image can be found at : https://github.com/clockworkpi/uConsole/tree/master/images

That'it , happy hacking 

