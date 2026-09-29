"""Verify healthy trial handoff observations, never infer watchdog reset success."""
from forge_boot_observation import verify as verify_boot


def verify(observed, previous, machine_id, nonce, timeout, watchdog):
    boot = verify_boot(observed, previous, machine_id, nonce)
    if type(timeout) is not int or not 60 <= timeout <= 300:
        raise ValueError('Invalid expected handoff timeout')
    values = [t for t in observed['cmdline'].split() if t.startswith('watchdog.open_timeout=')]
    if values != ['watchdog.open_timeout=' + str(timeout)]:
        raise ValueError('Firmware did not pass the exact watchdog handoff timeout')
    if (watchdog.get('early_watchdog') is not True
            or watchdog.get('open_timeout') != str(timeout) or watchdog.get('state') != 'active'
            or watchdog.get('identity') != 'Broadcom BCM2835 Watchdog timer'
            or watchdog.get('owner_pids') != [1]):
        raise ValueError('Expected active native watchdog owned by systemd')
    return dict(status='verified-healthy-watchdog-handoff', boot_id=boot['boot_id'],
                watchdog_timeout=timeout, failed_boot_fallback_qualified=False)
