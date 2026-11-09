# Published OS image verification

On 2026-09-20 all four Google Drive HTTPS downloads linked in the root README
were downloaded completely. Every byte count matched the response Content-Length,
and every MD5 matched the value already published in that README. The SHA-256
values in [os-images.sha256](os-images.sha256) were calculated from those payloads.
These are independently observed checksums, not manufacturer signatures or proof
of hardware compatibility.

From a directory containing the downloaded archives, verify them with:

```sh
sha256sum --check /path/to/uConsole/docs/os-images.sha256
```

| Archive | Downloaded bytes |
| --- | ---: |
| `uConsole_CM4_v3.1_64bit.img.bz2` | 2,212,739,229 |
| `uConsole_CM4_v0.1b_xfce_64bit.img.7z` | 1,341,856,417 |
| `uConsole_A06_v1.1e.img.7z` | 1,507,376,788 |
| `uConsole_R01_v1.3b.img.7z` | 1,545,723,326 |

## R01 v1.3b contents

The 7z archive extracted successfully, including its integrity check. The root
and boot partitions were inspected read-only, without booting the image:

- The root filesystem identifies itself as Ubuntu 22.04.1 LTS.
- `/etc/hostname` is `uConsole-R01`; `/etc/hosts` contains the same name on
  its IPv4 loopback entry. This establishes consistency in the shipped image;
  it does not prove that later desktop hostname changes update both files.
- The embedded kernel configuration enables `CONFIG_VT`,
  `CONFIG_FB_CONSOLE_SUNXI`, `CONFIG_FRAMEBUFFER_CONSOLE`, and
  `CONFIG_FRAMEBUFFER_CONSOLE_ROTATION`. The extlinux command line includes
  `console=tty0` and `fbcon=rotate:1`. No explicit cursor override was found in
  the inspected extlinux, grub, or uEnv boot configuration.
- The image includes historical journal entries reporting shutdown SIGTERM
  timeouts for several services, including user sessions, NetworkManager,
  Polkit, UDisks, Avahi, colord and the serial getty. This corroborates that
  slow shutdown occurred during image preparation, but does not isolate its
  cause or reproduce the issue on a current device. Do not treat shortening
  all stop timeouts as a verified fix.
- `/usr/src` contains no SDK source tree. The documented kernel rebuild still
  requires the separately obtained Allwinner SDK.

## CM4 v3.1 contents

The bzip2 archive decompressed successfully, including its integrity check.
Read-only inspection of its boot and root partitions established:

- The root filesystem is Debian 13.2 (Trixie), with Labwc 0.9.2 and libgpiod
  2.2.1 packages installed.
- Both `uconsole-kernel-cm4-rpi` 6.12.62-v8+ and
  `uconsole-kernel-cm5-rpi` 6.12.62-v8-16k+ are installed. Their corresponding
  module directories and `kernel8.img` / `kernel_2712.img` boot files exist.
- `config.txt` selects uConsole and VC4 overlays separately for Pi 4 and Pi 5.
  Applying the actual shipped overlays to the shipped CM4 and CM5 DTBs with
  Raspberry Pi dtmerge succeeds. Panel rotation, display enablement, 384 MiB
  CMA, and the corrected battery charge-current property pass the same checks
  used for the locally compiled kernels. CM4 I2C1 and SPI4 enablement also pass.
- No `devterm-fan-temp-daemon` package, matching service reference in the
  inspected systemd/package manifests, or matching file under the inspected
  service and local-program directories was found. Some other DevTerm-named
  packages are deliberately present (backlight and games); their names alone
  are not a reason to disable them.
- The hostname and hosts entry both name `clockworkpi`.

The shipped kernel versions have a trailing `+`; locally built artifact names
are recorded separately in the kernel build guides. This inspection does not
establish binary identity between the local builds and published image.

## Older CM4 and A06 contents

Both remaining 7z archives extracted successfully, including their integrity
checks. Their root partitions were inspected read-only:

- CM4 v0.1b is Debian 11 (Bullseye), with `uconsole-kernel-cm4-rpi` 0.13.
  No DevTerm fan package, service file or matching local Python script was
  found. A dangling enablement symlink remains at
  `/etc/systemd/system/multi-user.target.wants/devterm-fan-temp-daemon.service`,
  pointing to the absent `/etc/systemd/system/devterm-fan-temp-daemon.service`.
  Thus this archive cannot reproduce a running fan daemon's repeated
  `vcgencmd` failures, although the stale enablement link can be removed.
- A06 v1.1e identifies itself as Armbian 22.05.3 / Ubuntu 22.04. Its installed
  `uconsole-kernel-current-cpi-a06` package is 0.21, with modules for
  5.15.119-rockchip64. `/usr/src` is empty; the inspected vendor package tree
  exposes binary kernel packages but no kernel source package that would
  recover the unavailable Wi-Fi source needed by the historical build.
- A06 installs `devterm-fan-daemon-cpi-a06` 0.15 and enables
  `devterm-fan-temp-daemon-a06.service`. Its script controls a GPIO fan, but
  also changes CPU count, CPU frequencies/governor and GPU policy. This is a
  different implementation from the Raspberry Pi service in issue #5;
  removing it requires a separate decision about those performance settings.

No image was flashed and no target boot or peripheral acceptance was performed.
