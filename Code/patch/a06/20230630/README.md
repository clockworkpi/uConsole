# A06 kernel patch for uConsole

based on https://github.com/armbian/build.git 

git commit hash: 95c829f9e66451f2bff6f42ab3e61d211212c905

## How to

Use an isolated Ubuntu 22.04 (Jammy) build environment for this historical
Armbian revision. Minimal containers must install `lsb-release` before running
`compile.sh`: the ARM64 configuration uses it to select `qemu-x86_64-static`
for the Rockchip binary packing tools. Without it, U-Boot can compile but
packaging fails with `qemu-x86_64: command not found`.

The historical image uses Linux 5.15.119. To reproduce that version instead
of the moving `linux-5.15.y` branch, put
`KERNELBRANCH="tag:v5.15.119"` in `userpatches/lib.config` before compiling.

```
cd
git clone -b master https://github.com/armbian/build.git 

cd build
git checkout --detach 95c829f9e66451f2bff6f42ab3e61d211212c905

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






## Current build limitation

The pinned Armbian revision also fetches the `local_rtl8822bs` branch from
`150balbes/wifi`. On 2026-09-20 GitHub returned HTTP 404 for that repository.
The build script continues after the failed fetch and inserts a Kconfig include
for the absent driver, so Linux configuration fails. The isolated Jammy ARM64
run produced the U-Boot Debian package but did not produce a working kernel.
A verified replacement source or maintained driver integration is required;
the commands above are not currently an end-to-end successful kernel recipe.
