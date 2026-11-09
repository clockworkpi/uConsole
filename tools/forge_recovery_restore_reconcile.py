"""Strict classification of fresh, fenced restore observations.

No transport or retry: source-matched bytes are not proof that an uncertain
attempt completed, that a filesystem is clean, or that normal boot is safe.
"""
import copy

from forge_recovery_bootcommit import validate as validate_hold
from forge_recovery_commit_protocol import exact
from forge_recovery_commit_reconcile import check_inspection
from forge_recovery_hash import check_receipt
from forge_recovery_restore_ledger import request, checked_result
from forge_recovery_restore_plan import binding_checked


def check_fence(fenced,plan,pin,attempt,boot):
    requested = request(plan,pin,attempt)
    same_boot = boot==plan['binding']['boot_id']
    if not isinstance(fenced,dict): raise ValueError('Missing reconciliation fence')
    expected = dict(status='restore-fenced',plan_sha256=pin,attempt=attempt,boot_id=boot,
                    outcome=fenced.get('outcome'),stale_read_only_boot_unmounted=fenced.get('stale_read_only_boot_unmounted'),
                    stale_empty_mountpoint_removed=fenced.get('stale_empty_mountpoint_removed'),root_written=False,
                    boot_files_written=False,normal_boot_release_authorized=False)
    if (not exact(fenced,expected) or
            any(type(fenced[key]) is not bool for key in ('stale_read_only_boot_unmounted','stale_empty_mountpoint_removed')) or
            fenced['stale_read_only_boot_unmounted'] and fenced['stale_empty_mountpoint_removed']):
        raise ValueError('Reconciliation fence envelope differs')
    outcome = fenced['outcome']
    if not isinstance(outcome,dict): raise ValueError('Missing restore fence outcome')
    state = outcome.get('status')
    if same_boot:
        if state not in ('completed','incomplete','fenced-not-started'):
            raise ValueError('Unexpected same-boot restore fence outcome')
        expected = dict(status=state,request=requested,boot_id=boot,requires_new_recovery_boot=state=='incomplete',
                        root_write_authorized=False,normal_boot_release_authorized=False)
        if state=='completed': expected['result']=checked_result(outcome.get('result'),plan,pin)
    else:
        expected = dict(status='previous-boot-ended',request=requested,previous_boot_id=plan['binding']['boot_id'])
    if not exact(outcome,expected): raise ValueError('Restore fence outcome binding differs')
    return fenced


def check_restore_inspection(inspection,plan,pin,attempt,hold_plan,hold_pin,boot):
    fields = {'status','files','image','stage','plan_sha256','hold_plan_sha256','attempt','boot_id',
              'prefix','read_only','boot_unmounted','root_written','normal_boot_release_authorized'}
    if (not isinstance(inspection,dict) or set(inspection)!=fields or inspection['plan_sha256']!=pin or
            inspection['hold_plan_sha256']!=hold_pin or inspection['attempt']!=attempt or
            inspection['normal_boot_release_authorized'] is not False):
        raise ValueError('Restore inspection binding differs')
    converted = {key:value for key,value in inspection.items()
                 if key not in ('hold_plan_sha256','attempt','normal_boot_release_authorized')}
    converted.update(plan_sha256=hold_pin,deployment_authorized=False)
    check_inspection(converted,hold_plan,hold_pin,observed_boot_id=boot)
    return inspection


def classify(plan,pin,attempt,hold_plan,hold_pin,fenced,inspection,hash_plan,hash_observation):
    (plan,hold_plan,fenced,inspection,hash_plan,hash_observation) = copy.deepcopy(
        (plan,hold_plan,fenced,inspection,hash_plan,hash_observation))
    validate_hold(hold_plan,hold_pin)
    if (hold_pin!=plan['hold_plan_sha256'] or not exact(hold_plan['after'],plan['held_files']) or
            not exact(hold_plan['image_dependency'],plan['image_dependency'])):
        raise ValueError('Reconciliation hold evidence differs')
    binding_checked(hash_plan)
    if not exact({key:value for key,value in hash_plan.items() if key!='boot_id'},
                 {key:value for key,value in plan['binding'].items() if key!='boot_id'}):
        raise ValueError('Reconciliation target or layout differs')
    boot = hash_plan['boot_id']
    same_boot = boot==plan['binding']['boot_id']
    check_fence(fenced,plan,pin,attempt,boot)
    state = fenced['outcome']['status']
    check_restore_inspection(inspection,plan,pin,attempt,hold_plan,hold_pin,boot)
    if (not isinstance(hash_observation,dict) or hash_observation.get('status')!='verified-offline-storage-digests' or
            hash_observation.get('mode')!='physical' or
            any(hash_observation.get(key) is not False for key in
                ('is_backup','root_write_authorized','normal_boot_release_authorized','target_written'))):
        raise ValueError('Expected completed read-only physical hash observation')
    hashes = check_receipt(hash_observation['digests'],hash_plan['extent'],boot)
    source = exact(hashes['root'],plan['root_after'])
    before = exact(hashes['root'],plan['root_before'])
    conflicts = []
    if not exact(hashes['prefix'],inspection['prefix']): conflicts.append('inspection-prefix-changed')
    if not exact(hashes['prefix'],plan['prefix_guard']): conflicts.append('protected-prefix-changed')
    if not exact(hashes['suffix'],plan['suffix_guard']): conflicts.append('protected-suffix-changed')
    if inspection['status']!='after': conflicts.append('persistent-hold-not-intact')
    if state=='completed' and not source: conflicts.append('completed-receipt-root-differs')
    if state=='fenced-not-started' and not before: conflicts.append('unstarted-attempt-root-differs')
    status = ('conflict' if conflicts else 'source-matched' if source else 'before' if before else 'partial-or-diverged')
    return dict(status='reconciled-'+status,plan_sha256=pin,attempt=attempt,boot_id=boot,
                previous_boot_id=plan['binding']['boot_id'],fence=fenced,inspection=inspection,digests=hashes,
                root_matches_source=source,root_matches_before=before,conflicts=conflicts,
                original_attempt_completion_verified=state=='completed' and not conflicts,
                requires_new_recovery_boot=same_boot and state=='incomplete',observation_only=True,
                root_write_authorized=False,normal_boot_release_authorized=False,physical_restore_qualified=False)
