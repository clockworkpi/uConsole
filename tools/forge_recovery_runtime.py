"""RAM-only recovery initramfs resources. Not a deployable image by themselves.

The builder must provide busybox, kmod, udev, OpenSSH, wpa_supplicant, matching
modules/firmware, and private credentials. No target filesystem is auto-mounted.
The five-minute software deadline is not a hardware-watchdog guarantee.
"""

INIT = r'''#!/bin/sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
stage=mounts
fail() {
    echo "Forge recovery failed at $stage; returning to normal boot" >/dev/console
    # Console output alone is not a kernel log entry. A configured ramoops
    # console buffer can retain this marker across reboot. Never include WPA
    # logs, credentials, command arguments or arbitrary failure output here.
    if test -c /dev/kmsg; then
        (printf '<3>Forge recovery failed at %s; returning to normal boot\n' "$stage" >/dev/kmsg) 2>/dev/null || :
    fi
    reboot -f
    while :; do sleep 1; done
}
mkdir -p /proc /sys /dev || fail
mount -t proc proc /proc || fail
mount -t sysfs sysfs /sys || fail
mount -t devtmpfs devtmpfs /dev || fail
mkdir -p /run /tmp /dev/pts || fail
mount -t tmpfs -o mode=0755,nosuid,nodev tmpfs /run || fail
mount -t devpts devpts /dev/pts || fail
# This independent process bounds an otherwise healthy kernel whose networking
# or SSH startup stalls. It cannot recover a hung kernel or failed firmware.
lease_mode=0
case " $(cat /proc/cmdline) " in
    *" uconsole.recovery_lease=1 "*)
        lease_mode=1
        # Only independently armed timer readiness can retire this startup guard.
        ( sleep 300; test -s /run/forge-lease.ready || reboot -f ) &
        ;;
    *) ( sleep 300; reboot -f ) & ;;
esac
stage=credentials
case " $(cat /proc/cmdline) " in
    *" uconsole.recovery=1 "*) ;;
    *) fail ;;
esac
for file in /etc/forge/wpa.conf /etc/forge/authorized_keys /etc/forge/ssh_host_ed25519_key; do
    test -s "$file" || fail
done
mkdir -p /run/sshd
/usr/sbin/sshd -t -f /etc/forge/sshd_config || fail
stage=udev
/usr/lib/systemd/systemd-udevd --daemon || fail
udevadm trigger --action=add || fail
udevadm settle --timeout=20 || fail
case " $(cat /proc/cmdline) " in
    *" uconsole.recovery_watchdog=1 "*)
        stage=watchdog
        modprobe bcm2835_wdt || fail
        if test "$lease_mode" = 1; then
            /usr/bin/python3 -I -S /etc/forge/lease-launch.py >/run/forge-watchdog.log 2>&1 &
        else
            /usr/sbin/forge-watchdog >/run/forge-watchdog.log 2>&1 &
        fi
        keeper=$!
        attempts=0
        while ! test -s /run/forge-watchdog.ready; do
            kill -0 "$keeper" 2>/dev/null || fail
            attempts=$((attempts + 1))
            if test "$lease_mode" = 1; then
                test "$attempts" -le 15 || fail
            else
                test "$attempts" -le 5 || fail
            fi
            sleep 1
        done
        kill -0 "$keeper" 2>/dev/null || fail
        ;;
esac
if test "$lease_mode" = 1; then
    stage=lease-readiness
    test -s /run/forge-lease.ready || fail
fi
stage=network-driver
case " $(cat /proc/cmdline) " in
    *" uconsole.emulator=1 "*)
        network_interface=usb0
        modprobe cdc_ether || fail
        ;;
    *)
        network_interface=wlan0
        modprobe brcmfmac || fail
        ;;
esac
# Driver loading can finish before asynchronous firmware probing publishes the
# netdev. Bound this wait independently of the five-minute recovery deadline.
stage=network-interface
attempts=0
until ip link show dev "$network_interface" >/dev/null 2>&1; do
    test "$attempts" -lt 20 || fail
    attempts=$((attempts + 1))
    sleep 1
done
if test "$network_interface" = wlan0; then
    stage=rfkill
    for radio in /sys/class/rfkill/rfkill*; do
        test -f "$radio/type" || continue
        if test "$(cat "$radio/type")" = wlan; then
            echo 0 >"$radio/soft" || fail
        fi
    done
fi
stage=network-up
ip link set "$network_interface" up || fail
if test "$network_interface" = wlan0; then
    stage=wifi-authentication
    wpa_supplicant -B -i wlan0 -c /etc/forge/wpa.conf -f /run/wpa.log || fail
fi
printf '%s\n' "$network_interface" >/run/forge-interface || fail
udhcpc -f -q -i "$network_interface" -t 10 -T 3 -s /etc/forge/dhcp.sh >/run/dhcp.log 2>&1 &
/bin/sh /etc/forge/network-observer.sh &
if test "$lease_mode" = 1; then
    echo "Forge recovery: RAM root, SSH port 2222, renewable backup lease" >/dev/console
else
    echo "Forge recovery: RAM root, SSH port 2222, five-minute deadline" >/dev/console
fi
stage=ssh-server
/usr/sbin/sshd -D -e -f /etc/forge/sshd_config
fail
'''

NETWORK_OBSERVER = r'''#!/bin/sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
interface=$(cat /run/forge-interface) || exit 1
case "$interface" in wlan0|usb0) ;; *) exit 1;; esac
# Only fixed event-presence flags are exported. Never copy WPA/DHCP log text,
# SSIDs, addresses, credentials or command output into the persistent console.
# Samples occur at 0, 10, 30, 60, 120 and 240 seconds, before the boot deadline.
for delay in 0 10 20 30 60 120; do
    sleep "$delay"
    present=0 ipv4=0 associated=0 rejected=0 disabled=0 lease=0
    ip link show dev "$interface" >/dev/null 2>&1 && present=1
    ip -4 addr show dev "$interface" 2>/dev/null | grep -q 'inet ' && ipv4=1
    grep -q 'CTRL-EVENT-CONNECTED' /run/wpa.log 2>/dev/null && associated=1
    grep -q 'CTRL-EVENT-ASSOC-REJECT' /run/wpa.log 2>/dev/null && rejected=1
    grep -q 'CTRL-EVENT-SSID-TEMP-DISABLED' /run/wpa.log 2>/dev/null && disabled=1
    grep -q '^bound$' /run/forge-dhcp.state 2>/dev/null && lease=1
    message="Forge recovery network: present=$present ipv4=$ipv4 associated_seen=$associated assoc_reject_seen=$rejected auth_disabled_seen=$disabled lease_bound=$lease"
    printf '%s\n' "$message" >>/run/forge-network-status.log
    if test -c /dev/kmsg; then
        (printf '<2>%s\n' "$message" >/dev/kmsg) 2>/dev/null || :
    fi
done
'''

DHCP = r'''#!/bin/sh
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
# udhcpc supplies these values as environment variables, never shell source.
set -f
interface=${interface:-}
ip=${ip:-}
subnet=${subnet:-}
router=${router:-}
case "$interface" in wlan0|usb0) ;; *) exit 1;; esac
test "$interface" = "$(cat /run/forge-interface)" || exit 1
case "$1" in
    deconfig)
        ifconfig "$interface" 0.0.0.0 || exit 1
        printf '%s\n' deconfig >/run/forge-dhcp.state
        ;;
    bound|renew)
        test -n "$ip" && test -n "$subnet" || exit 1
        ifconfig "$interface" "$ip" netmask "$subnet" || exit 1
        # Inbound SSH does not need DNS. Use only the first offered router.
        for gateway in $router; do
            ip route replace default via "$gateway" dev "$interface" || exit 1
            break
        done
        printf '%s\n' bound >/run/forge-dhcp.state
        ;;
esac
'''

SSHD_CONFIG = '''Port 2222
ListenAddress 0.0.0.0
HostKey /etc/forge/ssh_host_ed25519_key
AuthorizedKeysFile /etc/forge/authorized_keys
PermitRootLogin prohibit-password
AllowUsers root
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
PermitEmptyPasswords no
UsePAM no
StrictModes yes
DisableForwarding yes
X11Forwarding no
PermitUserEnvironment no
PidFile /run/sshd.pid
LogLevel VERBOSE
'''
