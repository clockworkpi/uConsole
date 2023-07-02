# A06 kernel patch for uConsole

based on https://github.com/armbian/build.git 

git commit hash: 95c829f9e66451f2bff6f42ab3e61d211212c905

## How to

```
cd
git clone -b master https://github.com/armbian/build.git 

cd build

mkdir -p userpatches/kernel/rockchip64-current/

wget https://raw.githubusercontent.com/clockworkpi/uConsole/master/Code/patch/a06/20230630/drivers_network.sh.patch

wget https://raw.githubusercontent.com/clockworkpi/uConsole/master/Code/patch/a06/20230630/z-10000_a06_sound_230701.patch -O userpatches/kernel/rockchip64-current/z-10000_a06_sound_230701.patch
 
wget https://raw.githubusercontent.com/clockworkpi/uConsole/master/Code/patch/a06/20230630/z-10000_a06_uc_panel_230701.patch -O userpatches/kernel/rockchip64-current/z-10000_a06_uc_panel_230701.patch 

git apply  drivers_network.sh.patch

./compile.sh  BOARD=clockworkpi-a06 BRANCH=current BUILD_MINIMAL=no BUILD_DESKTOP=no BUILD_ONLY=u-boot,kernel,armbian-config,armbian-zsh,plymouth-theme-armbian,armbian-firmware,armbian-bsp KERNEL_CONFIGURE=no

```

## debs

Compile.sh will generate the following packages:

```
output/debs/linux-dtb-current-rockchip64_23.02.0-trunk_arm64.deb
output/debs/linux-image-current-rockchip64_23.02.0-trunk_arm64.deb
```

and are not able to be installed on the A06 OS image due to being masked.

The reason for masking these packages is to prevent the A06 kernel from being replaced/updated during the apt upgrade process.






