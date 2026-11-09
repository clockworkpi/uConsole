"""Internal root-only writable claim with protected card-range checks.

No public restore entrypoint. The owner executor must hold the Forge target
lock and independently qualify persistent recovery hold, retained backup,
source manifest and liveness. check() must freshly verify RAM boot identity
and return the bound layout. authorize() is a separate owner restore approval,
not an offline-backup lease receipt. O_EXCL does not stop a privileged raw
writer that deliberately ignores Linux claims.
"""
from contextlib import contextmanager
import copy
import os
import re

from forge_recovery_claim import RootClaim, block_identity


@contextmanager
def claim_restore_root(device, extent, guards, *, pin, boot_id, check, authorize, progress=None):
    if (not isinstance(device,str) or not re.fullmatch('/dev/mmcblk[0-9]{1,2}',device) or
            not isinstance(pin,str) or not re.fullmatch('[0-9a-f]{64}',pin) or
            not isinstance(boot_id,str) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot_id) or
            not callable(check) or not callable(authorize) or
            progress is not None and not callable(progress)):
        raise ValueError('Expected pinned root restore claim and owner callbacks')
    extent,guards=copy.deepcopy(extent),copy.deepcopy(guards)
    if (not isinstance(extent,dict) or extent.get('device')!=device+'p2' or
            any(type(extent.get(key)) is not int or extent[key]<=0 or extent[key]%512
                for key in ('offset_bytes','length_bytes','disk_bytes')) or
            extent['offset_bytes']+extent['length_bytes']>extent['disk_bytes'] or
            not isinstance(guards,dict) or set(guards)!={'prefix','root','suffix'}):
        raise ValueError('Expected exact root geometry and three protected range guards')
    start,length,total=extent['offset_bytes'],extent['length_bytes'],extent['disk_bytes']
    ranges=dict(prefix=(0,start),root=(start,length),suffix=(start+length,total-start-length))
    for name,(offset,size) in ranges.items():
        guard=guards[name]
        if (not isinstance(guard,dict) or set(guard)!={'offset','bytes','sha256'} or
                type(guard['offset']) is not int or guard['offset']!=offset or
                type(guard['bytes']) is not int or guard['bytes']!=size or
                not isinstance(guard['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',guard['sha256'])):
            raise ValueError('Restore guard geometry differs: '+name)
    if check()!=extent: raise ValueError('Recovery layout changed before restore authorization')
    if authorize(pin,boot_id)!={'restore':pin,'boot_id':boot_id}:
        raise ValueError('Explicit owner root-restore approval differs')
    # Approval may involve a host exchange; recheck before acquiring a writable
    # descriptor. Both opens below precede all writes and are checked again.
    if check()!=extent: raise ValueError('Recovery layout changed after restore authorization')
    root_fd=os.open(device+'p2',os.O_RDWR|os.O_EXCL|os.O_NOFOLLOW|os.O_NONBLOCK)
    claim=None
    try:
        block_identity(root_fd,device+'p2',length)
        card_fd=os.open(device,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        try:
            block_identity(card_fd,device,total)
            if check()!=extent: raise ValueError('Recovery layout changed after writable claim')
            claim=RootClaim(root_fd,card_fd,extent)
            claim.verify_guards(guards,progress=progress)
            # Includes another boot/layout check after a potentially long hash.
            if check()!=extent: raise ValueError('Recovery layout changed before yielding writable root')
            yield claim
            # The caller separately verifies the new root against the pinned
            # source. Boot, partition table, gaps and suffix must not change.
            if check()!=extent: raise ValueError('Recovery layout changed after root operation')
            claim.verify_guards({key:guards[key] for key in ('prefix','suffix')},progress=progress)
            if check()!=extent: raise ValueError('Recovery layout changed before claim release')
        finally:
            if claim is not None: claim.active=False
            os.close(card_fd)
    finally:
        os.close(root_fd)
