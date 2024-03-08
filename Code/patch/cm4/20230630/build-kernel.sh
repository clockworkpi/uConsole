#!/bin/bash

set -eox pipefail

# Create working directory
mkdir -p /uConsole
cd /uConsole

# Install dependencies
apt update

# Base kernel build dependencies
apt install -y build-essential \
               gcc-aarch64-linux-gnu \
               linux-headers-generic

# Install RPi kernel build dependencies
# [https://www.raspberrypi.com/documentation/computers/linux_kernel.html]
apt install -y git \
               bc \
               bison \
               flex \
               libssl-dev \
               make \
               libc6-dev \
               libncurses5-dev \
               crossbuild-essential-arm64

# Other possible usefull dependencies suggested by Ubuntu kernel building guide
# [https://wiki.ubuntu.com/Kernel/BuildYourOwnKernel]
apt install -y libncurses-dev \
               gawk \
               openssl \
               libssl-dev \
               dkms \
               libelf-dev \
               libudev-dev \
               libpci-dev \
               libiberty-dev \
               autoconf \
               llvm

# Clone Raspberry kernel sources
if [ ! -d "linux" ] ; then
    git clone https://github.com/raspberrypi/linux.git
fi
cd linux

# Linux repo may be already cloned in previous run so we need to remove
# any existing files modifications / applied patches
git clean -f

# Use specified source version
git reset --hard 3a33f11c48572b9dd0fecac164b3990fc9234da8

# Apply uConsole specific patch
git apply ../0001-patch-cm4.patch

# Config for Raspberry Pi 3, 3+, 4, 400 and Zero 2 W, and Raspberry Pi
# Compute Modules 3, 3+ and 4 default 64-bit build configuration:
# - bcm2711_defconfig
# - kernel8
#
# KERNEL=kernel8 make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- bcm2711_defconfig
# KERNEL=kernel8 make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- -j4
#
# [https://www.raspberrypi.com/documentation/computers/linux_kernel.html]

export KERNEL=kernel8
export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-

# Generate .config file
make bcm2711_defconfig

# Build kernel
make -j4

# Prepare placeholder for kernel modules
mkdir -p ./modules
rm -rf ./modules/*

# Deploy modules in predefined location
INSTALL_MOD_PATH=./modules make modules_install

# Remove unused module files
rm ./modules/lib/modules/*/build
rm ./modules/lib/modules/*/source

# Export build artifacts: kernel modules
mkdir -p ../modules
rm -rf ../modules/*

cp -rav ./modules/* ../modules

# Export build artifacts: kernel + device tree
mkdir -p ../out
rm -rf ../out/*
mkdir -p ../out/overlays

sudo cp arch/arm64/boot/Image ../out/$KERNEL.img
sudo cp arch/arm64/boot/dts/broadcom/*.dtb ../out
sudo cp arch/arm64/boot/dts/overlays/*.dtb* ../out/overlays/
sudo cp arch/arm64/boot/dts/overlays/README ../out/overlays/

