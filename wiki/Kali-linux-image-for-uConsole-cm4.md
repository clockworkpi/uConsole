# Kali linux image for uConsole cm4

we use kali-linux-2024.1-raspberry-pi-arm64.img   
and some original uConsole cm4 debs to make the image for uConsole CM4

## Here is the process:

```
sudo losetup -f --show -P kali-linux-2024.1-raspberry-pi-arm64.img
#assume we got /dev/loop1

sudo mount /dev/loop1p2 /mnt/p1/
sudo mount /dev/loop1p1 /mnt/p1/boot/

#mount chroot env
cd /mnt/p1
sudo mount --bind /dev dev/
sudo mount --bind /sys sys/
sudo mount --bind /proc proc/
sudo mount --bind /dev/pts dev/pts
#sudo chroot .

# remove kali kernel and headers (5.15.44)
sudo chroot /mnt/p1 /bin/bash -c 'sudo apt remove  kalipi-kernel kalipi-kernel-headers -y'

#add cpi apt source list
sudo chroot /mnt/p1/ /bin/bash -c 'wget -q -O- https://raw.githubusercontent.com/clockworkpi/apt/main/debian/KEY.gpg | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/clockworkpi.gpg'
sudo chroot /mnt/p1/ /bin/bash -c 'echo "deb https://raw.githubusercontent.com/clockworkpi/apt/main/debian/ stable main" | sudo tee  /etc/apt/sources.list.d/clockworkpi.list'
sudo chroot /mnt/p1/ /bin/bash -c 'sudo apt update'

#install the uconsole cm4 kernel
sudo chroot /mnt/p1/ /bin/bash -c 'sudo apt install -y uconsole-kernel-cm4-rpi'

# rotate lightdm
cat << EOF | sudo chroot /mnt/p1
cat <<EOL >/etc/lightdm/setup.sh
#!/bin/bash
xrandr --output DSI-1 --rotate right
exit 0
EOL
EOF

sudo chroot /mnt/p1/ /bin/bash -c 'sudo chmod +x /etc/lightdm/setup.sh'
sudo chroot /mnt/p1/ /bin/bash -c "sed  -i 's/^#greeter-setup-script=.*/greeter-setup-script=\/etc\/lightdm\/setup.sh/' /etc/lightdm/lightdm.conf"


#install 4G stuff !!
sudo chroot /mnt/p1 /bin/bash -c 'sudo apt install -y pppoe uconsole-4g-util-cm4'

#for 4G extension  !!
cat << EOF | sudo chroot /mnt/p1
cat << EOL > /etc/modprobe.d/blacklist-qmi.conf
blacklist qmi_wwan
blacklist cdc_wdm
EOL
EOF

# forbidden kali kernel package

cat << EOF | sudo chroot /mnt/p1
cat <<EOL > /etc/apt/preferences.d/kalipi-kernel
Package: kalipi-kernel
Pin: release *
Pin-Priority: -1
EOL
EOF

cat << EOF | sudo chroot /mnt/p1
cat <<EOL > /etc/apt/preferences.d/kalipi-kernel-headers
Package: kalipi-kernel-headers
Pin: release *
Pin-Priority: -1
EOL
EOF

#clear chroot env
cd -
sudo umount /mnt/p1/dev/pts
sudo umount /mnt/p1/dev
sudo umount /mnt/p1/proc
sudo umount /mnt/p1/sys
##clear bash 
sudo rm -rf /mnt/p1/root/.bash_history
sudo umount /mnt/p1/boot
sudo umount /mnt/p1

sudo losetup -D /dev/loop1

```

Now we can dd the img into a SD card bigger than 8GB   
`sudo dd if=kali-linux-2024.1-raspberry-pi-arm64.img of=/dev/sdcard bs=1M status=progress`
