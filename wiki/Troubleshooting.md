# uConsole troubleshooting

Record the core (CM4, CM5, A06 or R01), image name, kernel (`uname -r`),
`cat /etc/os-release`, and desktop/session before applying a fix. Images use
different desktop stacks; Xfce settings do not configure Labwc/Wayland.
These procedures address reported symptoms. Unless stated otherwise, they
have not yet been validated on the affected uConsole hardware.

## Nordic keyboard layouts (#46)

The keyboard sends USB HID key positions, and the host layout determines
which characters those positions produce. In an X11 desktop, select your
layout in the desktop keyboard settings. To try it for the current X11 session:

```sh
setxkbmap -layout se
```

Use `fi`, `dk`, or `no` for Finnish, Danish, or Norwegian. `setxkbmap -layout us`
restores the US layout for that session. On Wayland, use the compositor's
keyboard settings; `setxkbmap` does not configure the compositor's native
Wayland clients. On Debian-based images, `sudo dpkg-reconfigure keyboard-configuration` configures the system/console layout; the desktop
may have its own override.

The following base-layer mappings were verified with libxkbcommon and the
installed XKB layouts, using the key positions sent by the firmware:

| Host layout | Key marked `[` | Key marked `;` | Key marked `'` |
| --- | --- | --- | --- |
| Swedish / Finnish | å | ö | ä |
| Danish | å | æ | ø |
| Norwegian | å | ø | æ |

Shift produces the uppercase letters. This enables typing those letters but
does not change printed keycaps or add the extra ISO key. A physical Nordic
keyboard variant still requires a hardware/keycap design from the manufacturer.

## R01 text-console cursor (#2)

From a Linux virtual console, try the software cursor described by the
[Linux console documentation](https://docs.kernel.org/admin-guide/vga-softcursor.html):

```sh
printf '\033[?17;0;64c'
```

This requests a visible red software cursor. Restore the default with
`printf '\033[?0c'`. This sequence targets the Linux console, not an SSH
terminal emulator. For a persistent console-only setting, add this guard to
an existing shell login file (do not replace its other contents):

```sh
if [ "${TERM:-}" = linux ] && [ -t 1 ]; then
    printf '\033[?17;0;64c'
fi
```

Applications can reset cursor attributes. The kernel's `vt.cur_default`
parameter is another option, but the location of the boot command line differs
between R01 images. Inspect the image's boot configuration before editing it;
a Raspberry Pi `cmdline.txt` recipe does not apply automatically to the R01.

To diagnose why a desktop starts despite selecting multi-user mode, inspect:

```sh
systemctl get-default
systemctl status display-manager.service
systemctl list-dependencies multi-user.target
```

A desktop started by a shell profile or a custom service is independent of
the usual `graphical.target` dependency. Identify that launch path before
disabling services; keep a working console or SSH login available.

## Power button opens a menu instead of shutting down (#38)

First identify the session and event handler:

```sh
printf '%s / %s\n' "${XDG_CURRENT_DESKTOP:-unknown}" "${XDG_SESSION_TYPE:-unknown}"
systemd-inhibit --list
```

For **Xfce**, choose Shut down for the power button in Power Manager settings.
If instead you want systemd-logind to own the button, Xfce documents this
per-user setting (run it as the desktop user, not through sudo):

```sh
xfconf-query -c xfce4-power-manager \
  -p /xfce4-power-manager/logind-handle-power-key -n -t bool -s true
```

Then configure `HandlePowerKey=poweroff` under `[Login]` in a logind configuration
drop-in, such as `/etc/systemd/logind.conf.d/60-uconsole-power.conf`. Record the
previous setting first. Log out/in and reboot to apply the selected ownership
and logind configuration. To revert, restore the prior Xfce value (normally
`false`) and remove only the drop-in you added.

Changing `HandlePowerKey` alone can have no effect while a desktop inhibits
logind's handling of that key. This is documented in the
[Xfce power-manager FAQ](https://docs.xfce.org/xfce/xfce4-power-manager/faq).
For Labwc or another desktop, inspect that desktop's bindings/power manager
instead of applying the Xfce command. A clean shutdown action is different
from forcibly cutting power by holding the button.

## Power settings and long shutdowns (#11, #3)

An available desktop menu does not establish that the kernel, firmware and
hardware can suspend and resume. Inspect the capabilities without attempting
suspend:

```sh
cat /sys/power/state
cat /sys/power/mem_sleep 2>/dev/null
upower -e
systemd-inhibit --list
```

Missing battery information, unavailable suspend, and a delayed shutdown are
separate symptoms. For the R01 shutdown delay, capture the previous boot's
journal after a normal shutdown/reboot, if persistent logging is enabled:

```sh
journalctl -b -1 -o short-monotonic --no-pager
```

Look for the service or device named in a stop-job timeout. Record its unit
and surrounding timestamps before changing timeout settings. Reducing all
systemd stop timeouts can hide the fault and interrupt unfinished writes;
it does not identify why this board takes over two minutes to shut down.

## Obsolete DevTerm fan daemon (#5)

uConsole has no DevTerm fan to control. On an affected uConsole image, inspect
`systemctl status devterm-fan-temp-daemon.service`. If present, disable that
specific service:

```sh
sudo systemctl disable --now devterm-fan-temp-daemon.service
```

Do this on the uConsole, not on an actual DevTerm. The image-building recipe
should omit the DevTerm-only fan package/service. A missing unit means this
particular workaround is unnecessary. Read-only inspection of the complete
CM4 v3.1 image found no such package or service. The available CM4 v0.1b image
also lacks the service and script, but retains a dangling enablement symlink;
`systemctl disable` fails when the target unit no longer exists. For that
specific stale link, verify its target with `readlink`, then remove the link
and reload systemd:

```sh
readlink /etc/systemd/system/multi-user.target.wants/devterm-fan-temp-daemon.service
# Expected target: /etc/systemd/system/devterm-fan-temp-daemon.service
if [ ! -e /etc/systemd/system/devterm-fan-temp-daemon.service ] &&
   [ -L /etc/systemd/system/multi-user.target.wants/devterm-fan-temp-daemon.service ]; then
    sudo rm -- /etc/systemd/system/multi-user.target.wants/devterm-fan-temp-daemon.service
    sudo systemctl daemon-reload
fi
```

A06 v1.1e contains a different service,
`devterm-fan-temp-daemon-a06.service`, whose script also configures CPU count,
CPU frequency/governor and GPU policy. It is not the script reporting the
missing Raspberry Pi `vcgencmd` in issue #5. If removing it from an A06 image,
first decide which CPU/GPU policy should replace those settings; do not apply
the CM4 workaround by merely substituting its unit name. See the
[image inspection record](../docs/os-image-verification.md).

## Hostname changed but /etc/hosts did not (#1)

Inspect all three views of the hostname:

```sh
hostnamectl --static
cat /etc/hostname
cat /etc/hosts
getent hosts "$(hostname)"
```

On images using a `127.0.1.1` hostname entry, changing the static hostname
with the desktop network menu may leave the old name on that line. Back up
`/etc/hosts`, then use `sudoedit /etc/hosts` to replace the old machine name
on that entry with the new name. Preserve `127.0.0.1 localhost`, IPv6 entries,
and unrelated aliases. If another service manages this file, update its
configuration instead. Verify the result with `getent hosts "$(hostname)"`.

The inspected R01 v1.3b image instead puts `uConsole-R01` beside `localhost`
on the `127.0.0.1` line. On that image, replace only the old machine-name
alias on that line, preserving `localhost`; do not assume a `127.0.1.1`
entry exists.

The current CM4 pi-gen recipe writes both files when creating an image. That
does not prove the R01 desktop rename action keeps them synchronized; its
GUI/network-manager integration still needs reproduction on the affected OS.

## CM4 SPI and I2C expansion (#20)

Enabling a bus in `raspi-config` does not by itself establish which pins the
uConsole uses or whether those pins are already assigned. The pinned 6.12.62
CM4 `clockworkpi-uconsole` overlay enables I2C1 on GPIO44/45 for its ADC, I2C0
for the PMIC, and SPI4 with GPIO6/7 and GPIO4 chip select. The panel also uses
GPIO8 for reset and GPIO9 for backlight control. These are SoC GPIO numbers,
not expansion-connector pin numbers. The CM5 overlay differs; do not copy
CM4 pin assignments to CM5.

Inspect the active device tree and device nodes before choosing an expansion
bus:

```sh
ls -l /dev/i2c-* /dev/spidev*
ls /sys/class/i2c-adapter
ls /sys/bus/spi/devices
```

Compare the selected peripheral/pins with the board schematic and the active
`config.txt` overlays. If debugfs is mounted, the pinctrl `pinmux-pins` files
under `/sys/kernel/debug/pinctrl/` can identify pin owners. Do not repurpose
PMIC, panel, or backlight pins based on a generic Raspberry Pi pinout. The
current source enables buses; proving the reported external peripheral works
still requires its wiring, requested bus, and actual image configuration.

## Kodi display orientation (#13)

The current `panel-cwu50` driver exposes a native 720 by 1280 mode and a panel
orientation property; the CM4 overlay sets `rotation = <90>`. A desktop can
honor that property while a program using DRM/GBM directly handles rotation
differently. First record the Kodi version and display backend, and compare
Kodi's behavior with the desktop on the same image.

For an X11 session, `xrandr --query` shows the desktop output configuration.
For Wayland, use the compositor's output settings. Direct DRM/GBM Kodi does
not inherit X11 `xrandr` settings. Avoid applying multiple rotation changes
to the kernel, console and desktop at once: identify the backend and change
one layer, then verify touch/pointer coordinates and the application display.
The old v1.3g image from the issue still needs reproduction; the current
kernel's orientation metadata alone is not proof that Kodi is fixed.
