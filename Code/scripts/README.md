# Scripts code used in uConsole os image

## uconsole-4g-cm4

Bash script to PowerOn/PowerOff 4G ext module in uConsole.

## uconsole-4g-cm5

Power control for the CM5 4G extension, using `gpioset` from **libgpiod
2.x**. Install your distribution's GPIO command-line tools package (`gpiod`
on Debian-based systems), and check `gpioset --version`. Version 1.x is not
compatible. Run with permission to access `/dev/gpiochip0`:

```sh
sudo ./uconsole-4g-cm5 enable
sudo ./uconsole-4g-cm5 disable
```

This script implements the CM5 GPIO sequence reported in
[issue #44](https://github.com/clockworkpi/uConsole/issues/44). The sequence
still needs verification on the target hardware after changes. GPIO levels
after `gpioset` exits depend on the platform; these commands are specific to
the uConsole CM5 wiring and must not be treated as a generic GPIO controller.

Configure cellular connections separately with your preferred network tools.
The script works without systemd or ModemManager. It does not restart services,
select an APN, request a SIM PIN, or change routes. If using ModemManager,
`mmcli -L` checks detection after power-on. The former `status`, `connect`,
`disconnect`, `unlock`, `diagnose`, and `reset` commands have been removed;
use your modem/network manager for those operations.

## audio_3.5_patch.py

Python script to switch between 3.5 audio jack and speaker on uConsole when 3.5 audio jack plugged or not.

## uconsole-modem-flash.py

Shared Python 3 updater for the two SIM7600G22 firmware packages in `Bin/4G`.
Keep `modem-firmware-manifest.json` beside the script. Its SHA-256 values were
calculated from the firmware files in the repository's B04/B06 archives;
they detect missing, corrupted, or mixed package files, not hardware compatibility.

After extracting the appropriate package, check every partition without USB access:

```sh
python3 uconsole-modem-flash.py /path/to/LE20B04SIM7600G22_cpi_arm64 \
  --version LE20B04SIM7600G22 --verify-only
```

Once the matching SIM7600G22 modem is in fastboot mode, run the package's
`fastboot/bin/fastboot devices` and obtain its serial. Add `--serial SERIAL`
instead of `--verify-only` for a device preflight without writes. Add `--flash`
only when ready to write the firmware, with USB permissions (usually via sudo).
Use `--version LE20B06SIM7600G22` for the B06 package. The updater requires
exactly one fastboot device with the specified serial and stops on any command
error or timeout; it reboots only after all nine writes succeed. It does not
send `AT+BOOTLDR`, change USB mode, or automatically retry partial updates.

`--fastboot /absolute/path/to/fastboot` overrides the package executable.
The shipped binary is ARM64. `make modem-flash-tool` at the repository root
builds a native replacement in `build/modem-fastboot/bin/fastboot`. A checksum match and a fastboot serial do not
identify the modem model, so check the module/version against the firmware
guide before entering bootloader mode. Physical upgrades still require target validation.

## Desktop modem updater

The Tk desktop app uses the same backend, with package verification, device
selection, a required model confirmation, and an operation log. It enables
flashing only after a successful check of the current selection. Changing
the folder, firmware version, serial, or fastboot path invalidates that check.
Follow the firmware guide to enter fastboot mode before detecting the modem.

On Debian/Ubuntu, install `python3-tk` and `pkexec`, then build and install from
the repository root:

```sh
make modem-flash-tool
sudo make install-modem-updater
uconsole-modem-updater
```

The app also appears as **uConsole Modem Updater** in the desktop application
menu. Run it as your normal desktop user; device operations request administrator
authorization through your desktop's Polkit agent. File verification needs no
administrator access. The installed helper always uses its root-installed
fastboot executable; it rejects alternate executable paths. For a staging-only
installation, use `make install-modem-updater DESTDIR="$PWD/build/modem-updater-stage"`.

Logs are saved under `$XDG_STATE_HOME/uconsole-modem-updater`, defaulting to
`~/.local/state/uconsole-modem-updater`. The window cannot close during an
operation. A failure stops the backend; no automatic retry is attempted.
Keep power connected and inspect the log before deciding how to recover a
partial update. A successful write still requires checking USB mode and modem
connectivity using the firmware guide.

`make check-gui` exercises the desktop controls and worker failure handling
under Xvfb (requires `xvfb`, `xauth`, and system Python with Tk). These checks
do not access USB or write firmware. The GUI has also been visually checked
at 720 pixels wide; physical modem updates remain unverified.
