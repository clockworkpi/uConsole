## Get Kernel

Get kernel source from https://docs.aw-ol.com/d1/en/study/study_2getsdk/

The documented vendor download requires an Allwinner platform account and
SSH public-key enrollment. Obtain the SDK through that supported workflow;
this repository does not include it. The pinned kernel revision below and
the matching T-Head toolchain are required to reproduce this build. A generic
upstream RISC-V kernel or a different SDK revision is not equivalent to this
vendor tree. No matching public GitHub commit was found during the build
investigation; R01 compilation remains unverified until the SDK is available.

based on allwinner D1_Tina_Open/tina_d1_h_v2.1/lichee/linux-5.4

git commit hash: 2c3a14af7536e7a4be9f2b0550f04457ae585be6


after git apply the patch code

run 
```
export PATH=/data/tina_d1_h_v2.1/prebuilt/gcc/linux-x86/riscv/toolchain-thead-glibc/riscv64-glibc-gcc-thead_20200702/bin/:$PATH
cp arch/riscv/boot/dts/sunxi/uc_board.dts arch/riscv/boot/dts/sunxi/board.dts

make CROSS_COMPILE=riscv64-unknown-linux-gnu- ARCH=riscv sun20iw1p1_d1_r01_defconfig
make CROSS_COMPILE=riscv64-unknown-linux-gnu- ARCH=riscv -j2
make CROSS_COMPILE=riscv64-unknown-linux-gnu- ARCH=riscv INSTALL_MOD_PATH=test/rootfs/ modules_install
make CROSS_COMPILE=riscv64-unknown-linux-gnu- ARCH=riscv INSTALL_PATH=test/boot/ zinstall

```
to compile , you need to change the riscv64-glibc-gcc-thead_20200702 toolchain path to your real local path

after compiling you will get something like 

```
├── boot
│   ├── dt_board.dtb
│   ├── uc_board.dtb
│   └── vmlinuz-5.4.61+
└── rootfs
    └── lib
        └── modules
            └── 5.4.61+
```


