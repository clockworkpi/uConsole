"""Pinned observation-only entry point; never dispatches a root restore."""
import copy
import re

from forge_recovery_commit_protocol import exact
from forge_recovery_restore_plan import binding_checked
from forge_recovery_operation_contract import check_evidence
from forge_recovery_restore_ledger import request as check_attempt
from forge_recovery_restore_observe import fence, inspect
from forge_recovery_restore_protocol import write_header


def checked(request):
    request = copy.deepcopy(request)
    fields = {'protocol', 'plan', 'pin', 'inputs', 'attempt', 'query',
              'action', 'observed_boot_id', 'owner', 'accepted'}
    if (not isinstance(request, dict) or set(request) != fields or
            type(request['protocol']) is not int or request['protocol'] != 1 or
            request['action'] not in ('fence', 'inspect') or
            not isinstance(request['query'], str) or
            not re.fullmatch('[0-9a-f]{32}', request['query'])):
        raise ValueError('Unexpected restore observation request')
    plan, inputs = request['plan'], request['inputs']
    check_attempt(plan, request['pin'], request['attempt'])
    check_evidence(plan, request['pin'], inputs)
    binding = dict(plan['binding'], boot_id=request['observed_boot_id'])
    binding_checked(binding)
    if request['owner'] != binding['lease_owner']:
        raise ValueError('Restore observation owner differs')
    if ((request['action'] == 'fence' and request['accepted'] is not None) or
            (request['action'] == 'inspect' and not isinstance(request['accepted'], dict))):
        raise ValueError('Unexpected restore observation lease')
    return request


def run(request, channel):
    request = checked(request)
    # No interactive stream, trailing commands, or lease renewal while mounted.
    # Require EOF before even fencing, not merely before reporting success.
    if channel.read(1):
        raise ValueError('Trailing restore observation input')
    args = (request['plan'], request['pin'], request['attempt'])
    keywords = dict(observed_boot_id=request['observed_boot_id'])
    if request['action'] == 'fence':
        result = fence(*args, **keywords)
    else:
        inputs = request['inputs']
        result = inspect(*args, inputs['hold_plan'], inputs['hold_pin'], **keywords,
                         owner=request['owner'], accepted=request['accepted'])
    write_header(channel, dict(type='restore-observation', query=request['query'],
        action=request['action'], plan_sha256=request['pin'], attempt=request['attempt'],
        boot_id=request['observed_boot_id'], result=result))
    channel.flush()
