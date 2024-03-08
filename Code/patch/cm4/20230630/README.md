# uConsole CM4 kernel sources
uConsole kernel is based on Raspberry Pi kernel sources:
`https://github.com/raspberrypi/linux.git`

The current version is based on top of commit hash:
`3a33f11c48572b9dd0fecac164b3990fc9234da8`


# Build
Kernel build can be performed on Ubuntu 22.04 with aarch64 cross compilation.
To automate this process `build-kernel.sh` script has been created.
It will automatically install all needed dependencies on for kernel building
both from RPi and Ubuntu guidelines.

On top of the RPi kernel - uConsole specific patch is applied. It contains
implementation of all required drivers like video driver for uConsole screen.

To automate build process on environmend different than Ubuntu 22.04 `run.sh`
script has been created. It utilizes docker container for building the kernel
using build script.

Final results are located in `out` directory in the source tree.


# Deploy
To deploy build result use content of `out` directory and copy it over
to the right locations on SD card. More you can find in official RPi guide:
https://www.raspberrypi.com/documentation/computers/linux_kernel.html


# Kernel configuration
In order to use compiled kernel8.img with CM4 in uConsole, we have to setup
a `config.txt` configuration file. It contains various options which will be
passed to runtime kernel for proper uConsole specific drivers initialization.



