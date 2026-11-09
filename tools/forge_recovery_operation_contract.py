"""Select and recompile complete evidence for a root operation, never authorize it."""
import copy
import os

from forge_recovery_commit_protocol import exact
from forge_recovery_deploy_plan import INPUT_NAMES as DEPLOY_INPUTS, compile_plan as compile_deploy
from forge_recovery_restore_plan import INPUT_NAMES as RESTORE_INPUTS, compile_plan as compile_restore
from forge_recovery_restore_source import digest, sha
from forge_recovery_source_contract import BACKUP, DERIVATIVE
from forge_target_journal import private_directory, read_record


def source_kind(plan):
    if isinstance(plan, dict):
        pair = (plan.get('kind'), plan.get('operation'))
        if pair == ('guarded-backup-root-restore-plan', 'restore-backup-root'):
            return BACKUP
        if pair == ('guarded-derived-root-deploy-plan', 'deploy-derived-root'):
            return DERIVATIVE
    raise ValueError('Unsupported root operation kind')


def check_evidence(plan, pin, inputs):
    plan, inputs = copy.deepcopy((plan, inputs))
    if not sha(pin) or digest(plan) != pin:
        raise ValueError('Root operation differs from pinned owner plan')
    kind = source_kind(plan)
    names, compiler, source_name = ((RESTORE_INPUTS, compile_restore, 'source_manifest')
        if kind == BACKUP else (DEPLOY_INPUTS, compile_deploy, 'derivative'))
    if not isinstance(inputs, dict) or set(inputs) != set(names):
        raise ValueError('Root operation source evidence set differs')
    rebuilt = compiler(*(inputs[name] for name in names))
    if not exact(plan, rebuilt):
        raise ValueError('Root operation differs from recompiled evidence')
    return kind, inputs[source_name]


def load(directory, pin):
    """Read a private journal and recompile its exact owner-pinned operation."""
    fd = private_directory(directory)
    try:
        plan = read_record(fd, 'plan.json')
        inputs = read_record(fd, 'inputs.json')
    finally:
        os.close(fd)
    check_evidence(plan, pin, inputs)
    return plan
