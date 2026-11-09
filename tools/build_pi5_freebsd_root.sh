#!/bin/sh
# SPDX-License-Identifier: BSD-2-Clause
# Build a private Pi5 simulator multi-user root on an allocated FreeBSD worker.
# Inputs and outputs are regular files; never installs to a running root/disk.
set -eu
usage() {
 echo 'usage: build_pi5_freebsd_root.sh -i aarch64.iso -s ISO_SHA256 -k kernel -K KERNEL_SHA256 -o NEW_OUTPUT_DIRECTORY' >&2
 exit 64
}
iso='' kernel='' isohash='' kernelhash='' out=''
while getopts i:s:k:K:o: opt; do
 case $opt in
 i) iso=$OPTARG;; s) isohash=$OPTARG;; k) kernel=$OPTARG;; K) kernelhash=$OPTARG;; o) out=$OPTARG;; *) usage;;
 esac
done
if [ -z "$iso" ] || [ -z "$kernel" ] || [ -z "$isohash" ] || [ -z "$kernelhash" ] || [ -z "$out" ]; then
 usage
fi
[ "$(uname -s)" = FreeBSD ] || { echo 'Run on the allocated FreeBSD worker' >&2; exit 1; }
[ "$(id -u)" = 0 ] || { echo 'Root is required to retain image UID-0 ownership' >&2; exit 1; }
if [ ! -f "$iso" ] || [ ! -f "$kernel" ]; then
 echo 'ISO and kernel must be regular files' >&2; exit 1
fi
[ ! -e "$out" ] || { echo 'Refusing existing output directory' >&2; exit 1; }
[ "$(sha256 -q "$iso")" = "$isohash" ]
[ "$(sha256 -q "$kernel")" = "$kernelhash" ]
readelf -h "$kernel" | grep -q 'AArch64'
mkdir -p "$out/root"
root=$out/root
# Retain the actual base startup, login and shared-library implementation.
# No custom /etc/rc or rescue-shell replacement constitutes this multi-user boot.
tar -xf "$iso" -C "$root" bin sbin lib libexec etc usr/bin usr/sbin usr/lib usr/libexec usr/share/misc usr/share/zoneinfo var rescue
mkdir -p "$root/dev" "$root/tmp" "$root/mnt" "$root/proc" "$root/boot/kernel"
chmod 1777 "$root/tmp"
cat > "$root/etc/rc.conf" <<'EOF'
hostname="pi5-sim"
network_interfaces="lo0"
ifconfig_lo0="inet 127.0.0.1"
ifconfig_lo0_ipv6="inet6 ::1 prefixlen 128"
sshd_enable="NO"
sendmail_enable="NONE"
cron_enable="NO"
background_fsck="NO"
clear_tmp_enable="NO"
devmatch_enable="NO"
EOF
cat > "$root/etc/fstab" <<'EOF'
/dev/gpt/PI5ROOT / ufs rw 1 1
EOF
cat > "$root/etc/ttys" <<'EOF'
console none unknown off secure
ttyu0 "/usr/libexec/getty 3wire" vt100 on secure
EOF
# Private console-only guest with no emulated NIC. Keep real getty/login and
# password database code; no scripted shell is substituted for init/login.
sed 's/^root:[^:]*:/root::/' "$root/etc/master.passwd" > "$out/master.passwd"
install -m 600 "$out/master.passwd" "$root/etc/master.passwd"
pwd_mkdb -p -d "$root/etc" "$root/etc/master.passwd"
rm "$out/master.passwd"
install -m 555 "$kernel" "$root/boot/kernel/kernel"
makefs -t ffs -B little -o version=2,label=PI5ROOT -s 1200m "$out/root.ufs" "$root"
mkimg -s gpt -c 2g -p "freebsd-ufs/PI5ROOT:=$out/root.ufs" -o "$out/pi5-multiuser.img"
sha256 "$iso" "$kernel" "$out/pi5-multiuser.img" "$root/etc/rc" "$root/sbin/init" "$root/usr/libexec/getty" "$root/usr/bin/login" > "$out/identities.txt"
xz -T4 -k "$out/pi5-multiuser.img"
