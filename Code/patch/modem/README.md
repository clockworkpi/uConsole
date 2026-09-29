# Native modem fastboot build

`make modem-flash-tool` at the repository root extracts the SIMCOM fastboot
source included in `Bin/4G/LE20B04SIM7600G22_cpi_arm64.tar.gz`, applies
`fastboot-path-bounds.patch`, and rebuilds all objects for the host. The
executable is `build/modem-fastboot/bin/fastboot`. The B04 firmware archive
is used only as the existing source distribution; no modem firmware is
written during the build.

The patch replaces unbounded path formatting in USB enumeration with checked
`snprintf` calls. Overlong paths are skipped. This removes the compiler's
format-overflow warnings without changing the firmware protocol or partition
sequence. The native ARM64 build and `fastboot help` were verified; hardware
USB enumeration and flashing remain unverified.

Pass the resulting binary to `uconsole-modem-flash.py --fastboot` if the
prebuilt binary in the firmware package does not match the host architecture.
