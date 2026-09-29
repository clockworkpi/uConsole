"""Emulator-only host qualification hooks; no physical fault-injection route."""
import json
import copy
from pathlib import Path
from unittest.mock import patch

import forge_recovery_commit_transport as transport
from forge_recovery_commit_protocol import Protocol
import forge_recovery_commit_reconcile as reconciliation


KILL_AFTER_WRITE = r'''
_fixture_files_commit=files_commit
def files_commit(plan,point):
    if plan['binding']['mode']!='emulated' or 'uconsole.emulator=1' not in Path('/proc/cmdline').read_text().split():
        raise ValueError('CONFIG failure injection is emulator-only')
    _fixture_files_commit(plan,point)
    # Leave the completed CONFIG write mounted, with no ledger receipt.
    os.kill(os.getpid(),signal.SIGKILL)
    raise RuntimeError('SIGKILL unexpectedly returned')
'''

HANGUP_INSPECTOR = r'''
_fixture_inspect_files=inspect_files
def inspect_files(plan,point):
    if plan['binding']['mode']!='emulated' or 'uconsole.emulator=1' not in Path('/proc/cmdline').read_text().split():
        raise ValueError('Inspector hangup injection is emulator-only')
    _fixture_inspect_files(plan,point)
    os.kill(os.getpid(),signal.SIGHUP)
    raise RuntimeError('Inspector hangup unexpectedly returned')
'''


class InjectedReplyLoss(RuntimeError):
    pass


def dispatch(probe,journal,pin,lease,*,authorize,fault):
    if probe.mode!='emulated' or fault not in ('kill-after-write','lose-completion'):
        raise ValueError('Commit interruption requires an explicit emulator fixture')
    original_payload,original_consume=transport.payload,Protocol.consume
    def payload(*args,**kwargs):
        request=original_payload(*args,**kwargs)
        if request['plan']['binding']['mode']!='emulated': raise ValueError('Fault plan is not emulated')
        if fault=='kill-after-write':
            request['modules']=[(name,source+KILL_AFTER_WRITE if name=='forge_recovery_bootcommit' else source)
                                for name,source in request['modules']]
        return request
    def consume(self,message):
        if fault=='lose-completion' and message.get('type')=='complete':
            raise InjectedReplyLoss('Discarded emulator completion after durable guest receipt')
        return original_consume(self,message)
    with patch.object(transport,'payload',side_effect=payload),patch.object(Protocol,'consume',consume):
        try:
            transport.dispatch(probe,journal,pin,lease,authorize=authorize)
        except RuntimeError as exc:
            if fault=='lose-completion' and not isinstance(exc,InjectedReplyLoss): raise
            reason=type(exc).__name__+': '+str(exc)
        else:
            raise RuntimeError('Requested emulator interruption did not occur')
    acceptance=json.loads((Path(journal)/'commit-attempt/acceptance.json').read_text())
    if acceptance['status']!='uncertain' or acceptance['plan_sha256']!=pin:
        raise ValueError('Interrupted commit did not retain uncertain intent')
    return dict(fault=fault,error=reason,original_acceptance=acceptance)


def interrupt_inspector(probe,journal,pin,lease):
    if probe.mode!='emulated': raise ValueError('Inspector interruption requires an emulator fixture')
    journal=Path(journal)
    previous=set(journal.glob('reconcile-*'))
    original=reconciliation.exchange
    def exchange(argv,request,*args,**kwargs):
        request=copy.deepcopy(request)
        if request['plan']['binding']['mode']!='emulated': raise ValueError('Inspector fault plan is not emulated')
        if request['action']=='inspect':
            request['modules']=[(name,source+HANGUP_INSPECTOR if name=='forge_recovery_commit_inspect' else source)
                                for name,source in request['modules']]
        return original(argv,request,*args,**kwargs)
    with patch.object(reconciliation,'exchange',side_effect=exchange):
        try:
            reconciliation.reconcile(probe,journal,pin,lease)
        except RuntimeError:
            pass
        else:
            raise RuntimeError('Inspector hangup did not interrupt the observation')
    created=set(journal.glob('reconcile-*'))-previous
    if len(created)!=1: raise ValueError('Ambiguous interrupted inspector evidence')
    directory=created.pop()
    acceptance=json.loads((directory/'acceptance.json').read_text())
    if (acceptance['status']!='incomplete' or acceptance['plan_sha256']!=pin or
            'Commit inspection disconnected; retain incomplete evidence' not in (directory/'inspect-stderr.log').read_text()):
        raise ValueError('Inspector failed for an unrelated reason')
    probe.inspect(expected_boot_id=lease.boot_id)  # Strictly RAM-only again.
    return dict(status='interrupted-inspector-unmounted',evidence_directory=str(directory),
                original_acceptance=acceptance,physical_qualified=False)
