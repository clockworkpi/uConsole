# A06 kernel patch for uConsole

based on https://github.com/armbian/build.git 

git commit hash: 95c829f9e66451f2bff6f42ab3e61d211212c905

## How to

```
cd
git clone -b master https://github.com/armbian/build.git 

cd build

mkdir -p userpatches/kernel/rockchip64-current/

wget drivers_network.sh.patch

wget z-10000_a06_sound_230701.patch
 
wget z-10000_a06_uc_panel_230701.patch

git apply  drivers_network.sh.patch


./compile.sh

```


