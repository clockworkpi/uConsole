"""Owner-pinned boot-selector jobs; no root writes, retries or implicit reboot."""
import os
from pathlib import Path

from forge_recovery_bootcommit import validate
from forge_recovery_bootplan import compile_transition, digest, prepare as prepare_plan
from forge_recovery_commit_protocol import exact
from forge_target_journal import private_directory, read_record


def load_preparation(job):
    """Compile pinned host evidence, taking the root guard from a source manifest."""
    from forge_recovery_source_contract import validate as validate_source
    args = job.arguments
    fd = private_directory(args['input_directory'])
    try:
        value = read_record(fd, 'inputs.json')
    finally:
        os.close(fd)
    fields = {'schema', 'hold_review', 'hash_plan', 'hash_observation',
              'source_manifest', 'source_manifest_sha256', 'source_kind'}
    if (not isinstance(value, dict) or set(value) != fields or type(value['schema']) is not int or
            value['schema'] != 1 or digest(value) != args['input_sha256']):
        raise ValueError('Hold preparation inputs differ from owner approval')
    source = validate_source(value['source_manifest'], value['source_manifest_sha256'],
                             expected_kind=value['source_kind'])
    compiled = compile_transition(value['hold_review'], value['hash_plan'], value['hash_observation'],
                                  source['root'], args['transition'])
    b = compiled['binding']
    if (compiled['before']['machine_id'] != job.machine_id or b.get('boot_id') != job.boot_id or
            b.get('lease_owner') != job.owner or b.get('mode') != 'physical' or
            any(b.get(key) != getattr(job.probe, key) for key in ('nonce', 'kernel', 'serial'))):
        raise ValueError('Hold preparation inputs belong to another physical session')
    return value


def prepare(job, inputs):
    """Publish a private draft; the owner must separately approve its new pin."""
    result = prepare_plan(job.arguments['destination'], inputs['hold_review'], inputs['hash_plan'],
                          inputs['hash_observation'], inputs['source_manifest']['root'], job.arguments['transition'])
    return dict(status='prepared-not-approved', plan_sha256=result['plan_sha256'],
                transition=job.arguments['transition'], input_sha256=job.arguments['input_sha256'],
                source_manifest_sha256=inputs['source_manifest_sha256'],
                root_sha256=inputs['source_manifest']['root']['sha256'],
                root_bytes=inputs['source_manifest']['root']['bytes'],
                target_written=False, deployment_authorized=False, normal_boot_release_authorized=False)


def load(job):
    """Check the complete local plan before contacting the recovery target."""
    args = job.arguments
    directory = Path(args['journal'])
    fd = private_directory(directory)
    try:
        plan = validate(read_record(fd, 'plan.json'), args['plan_sha256'])
    finally:
        os.close(fd)
    b = plan['binding']
    transition = args['transition'] if job.operation == 'reconcile-hold' else job.operation
    if (plan['operation'] != transition or plan['before']['machine_id'] != job.machine_id or
            any(b.get(key) != getattr(job.probe, key) for key in ('mode', 'nonce', 'kernel', 'serial')) or
            b.get('mode') != 'physical' or b.get('lease_owner') != job.owner or
            (job.operation != 'reconcile-hold' and b.get('boot_id') != job.boot_id) or
            plan['root_guard']['sha256'] != args['root_sha256'] or
            plan['root_guard']['bytes'] != args['root_bytes']):
        raise ValueError('Hold job differs from owner-approved transition, identity or root guard')
    if job.operation != 'reconcile-hold' and os.path.lexists(directory/'commit-attempt'):
        raise FileExistsError('Hold commit already attempted; reconcile its retained evidence, never redispatch')
    return plan


def execute(job, lease, plan):
    args = job.arguments
    if job.operation == 'reconcile-hold':
        from forge_recovery_commit_reconcile import reconcile
        result = reconcile(job.probe, args['journal'], args['plan_sha256'], lease,
                           observed_boot_id=job.boot_id)
        # Private file preimages and host evidence paths remain in the journal.
        return {key: result[key] for key in ('status', 'query', 'attempt', 'plan_sha256',
                'deployment_authorized', 'root_written', 'normal_boot_release_authorized') if key in result}
    from forge_recovery_commit_transport import dispatch
    def authorize(reviewed):
        validate(reviewed, args['plan_sha256'])
        if not exact(reviewed, plan):
            raise ValueError('Hold commit changed after owner review')
        return dict(commit=args['plan_sha256'], boot_id=job.boot_id)
    return dispatch(job.probe, args['journal'], args['plan_sha256'], lease, authorize=authorize)
