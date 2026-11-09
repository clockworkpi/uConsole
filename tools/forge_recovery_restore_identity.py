"""Fresh local physical restore preconditions, not restore authorization.

Bracket the MMC observation with physical RAM/firmware observations. Call this
again after long hashes and host exchanges; retained planning evidence cannot
establish the identity of a running worker. No mount or write is performed.
"""
import contextlib
import copy
import io
import json

from forge_ram_identity import READER
from forge_ram_boot_observation import SOURCE, checked
from forge_recovery_layout import reader, root_extent
from forge_recovery_restore_plan import binding_checked


def observe_local(source):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(source, {})
    return json.loads(output.getvalue())


def checker(binding, *, observe=observe_local):
    """Create a frozen, repeatable local check for an installed owner executor.

    observe is a trusted local reader injection for tests, never a callback
    supplied in a client request. This is not the persistent-file inspection,
    watchdog check, exclusive claim, or owner's explicit write approval.
    """
    binding = copy.deepcopy(binding)
    binding_checked(binding)
    if not callable(observe):
        raise ValueError('Expected local recovery observation reader')
    ram_source = 'identity_reader=' + repr(READER) + '\n' + SOURCE
    layout_source = reader(binding['device'])

    def check_ram():
        record = observe(ram_source)
        checked(record, binding['nonce'], binding['kernel'], binding['serial'],
                boot_id=binding['boot_id'], expected_tryboot=0)
        tokens = record['identity']['cmdline'].split()
        for prefix, value in (('uconsole.recovery_owner=', binding['lease_owner']),
                              ('uconsole.recovery_lease=', '1')):
            if [token for token in tokens if token.startswith(prefix)] != [prefix + value]:
                raise ValueError('Physical restore recovery lease selection differs')

    def check():
        check_ram()
        extent = root_extent(observe(layout_source), binding['cid'], binding['disk_id'],
                             device=binding['device'])
        if extent != binding['extent']:
            raise ValueError('Physical restore layout changed')
        check_ram()
        return extent

    return check
