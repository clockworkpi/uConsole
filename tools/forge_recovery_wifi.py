"""Private WPA2-PSK recovery configuration; never publish or log credentials."""
import hashlib
import configparser
import os
from pathlib import Path
import re
import stat
import subprocess


def default_pmf(configuration):
    """Resolve NM's built-in default only when no merged PMF override exists.

    Conditional connection sections require NM's matching/priority semantics;
    reject them rather than guessing whether their PMF setting applies.
    """
    if not isinstance(configuration, str) or not 0 < len(configuration) <= 65536:
        raise ValueError('Missing or oversized merged NetworkManager configuration')
    parsed = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parsed.read_string(configuration)
    except configparser.Error:
        raise ValueError('Cannot parse merged NetworkManager configuration') from None
    if not parsed.sections() or parsed.defaults():
        raise ValueError('Unsupported merged NetworkManager defaults')
    for section in parsed.sections():
        for key in parsed[section]:
            if key == 'pmf' or key.endswith('.pmf'):
                raise ValueError('Global PMF override requires explicit effective-policy resolution')
    # NM documents optional PMF when neither profile nor global config sets it.
    return '2'


def compile_config(ssid, secret, key_mgmt, pmf, protocol, *, resolved_default=None):
    if key_mgmt != 'wpa-psk' or pmf not in ('0', '1', '2', '3') or protocol not in ('', 'rsn'):
        raise ValueError('Recovery currently requires a WPA2-PSK profile')
    if not isinstance(ssid, str) or any(c in ssid for c in ('\0', '\n', '\r')):
        raise ValueError('Unsupported recovery SSID')
    network = ssid.encode('utf-8')
    if not 1 <= len(network) <= 32:
        raise ValueError('Recovery SSID must be 1..32 bytes')
    if not isinstance(secret, str):
        raise ValueError('Missing WPA-PSK credential')
    if re.fullmatch('[0-9a-fA-F]{64}', secret):
        psk = secret.lower()
    elif 8 <= len(secret) <= 63 and all(32 <= ord(c) <= 126 for c in secret):
        psk = hashlib.pbkdf2_hmac('sha1', secret.encode('ascii'), network, 4096, 32).hex()
    else:
        raise ValueError('Unsupported WPA-PSK credential encoding')
    if pmf == '0':
        if resolved_default not in ('1', '2', '3'):
            raise ValueError('Resolve the effective PMF default before compiling recovery Wi-Fi')
        pmf = resolved_default
    management = {'1': 0, '2': 1, '3': 2}[pmf]
    return ('network={\n ssid=' + network.hex() + '\n psk=' + psk +
            '\n key_mgmt=WPA-PSK\n proto=RSN\n ieee80211w=' + str(management) + '\n}\n').encode()


def capture(directory, connection, machine_id):
    if not re.fullmatch('[0-9a-f-]{36}', connection) or not re.fullmatch('[0-9a-f]{32}', machine_id):
        raise ValueError('Invalid expected target identity')
    if os.geteuid() != 0 or Path('/etc/machine-id').read_text().strip() != machine_id:
        raise ValueError('Credential capture requires the expected target as root')
    directory = Path(directory)
    parent = directory.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise ValueError('Credential parent must be a private root-owned real directory')
    def query(arguments):
        result = subprocess.run(['nmcli', '--escape', 'no', *arguments], capture_output=True,
                                text=True, timeout=10)
        if result.returncode or len(result.stdout) > 65536:
            raise RuntimeError('Network profile capture failed')
        return result.stdout.removesuffix('\n')
    def active():
        if query(['-g', 'GENERAL.CON-UUID', 'device', 'show', 'wlan0']) != connection:
            raise ValueError('Active Wi-Fi profile differs from approved profile')
    def field(name, secret=False):
        return query((['--show-secrets'] if secret else []) + ['-g', name, 'connection', 'show', 'uuid', connection])
    active()
    names = ('802-11-wireless.ssid', '802-11-wireless-security.key-mgmt',
             '802-11-wireless-security.pmf', '802-11-wireless-security.proto',
             '802-11-wireless-security.group', '802-11-wireless-security.pairwise')
    settings = [field(name) for name in names]
    if any(settings[4:]):
        raise ValueError('Explicit cipher restrictions require separate recovery configuration')
    secret = field('802-11-wireless-security.psk', True)
    def merged_config():
        result = subprocess.run(['/usr/sbin/NetworkManager', '--print-config'],
                                capture_output=True, text=True, timeout=10)
        if result.returncode or len(result.stdout) > 65536:
            raise RuntimeError('Cannot capture merged NetworkManager policy')
        return result.stdout
    configuration = merged_config() if settings[2] == '0' else None
    effective = default_pmf(configuration) if configuration is not None else settings[2]
    payload = compile_config(settings[0], secret, *settings[1:4], resolved_default=effective)
    # Check every sampled property again, not merely the active UUID.
    again = [field(name) for name in names]
    if again != settings or field('802-11-wireless-security.psk', True) != secret:
        raise ValueError('Wi-Fi profile changed during credential capture')
    if configuration is not None and merged_config() != configuration:
        raise ValueError('NetworkManager policy changed during credential capture')
    active()
    directory.mkdir(mode=0o700)  # Exclusive, records original absence.
    fd = os.open(directory/'wpa.conf', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return {'status': 'private-wifi-captured', 'connection': connection,
            'machine_id': machine_id, 'contains_private_credentials': True,
            'effective_pmf': effective,
            'network_qualified': False, 'boot_partition_changed': False}
