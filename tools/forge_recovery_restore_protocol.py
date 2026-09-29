"""Bounded restore framing for an already authorized root writer.

The enclosing executor owns live boot/hold/lease checks, timeouts, fencing and
protected-range verification. This byte protocol supplies none of that authority.
Host source completion is distinct from target-root verification and boot release.
"""
import hashlib
import json
import re

from forge_recovery_commit_protocol import decode, exact
from forge_recovery_source_contract import BACKUP, validate, completion as source_completion

MAX_HEADER = 4096


def checked_binding(binding, pin):
    fields={'plan_sha256','manifest_sha256','attempt','boot_id'}
    if (not isinstance(binding,dict) or set(binding)!=fields or binding['manifest_sha256']!=pin or
            any(not isinstance(binding[key],str) or not re.fullmatch('[0-9a-f]{64}',binding[key])
                for key in ('plan_sha256','manifest_sha256')) or
            not isinstance(binding['attempt'],str) or not re.fullmatch('[0-9a-f]{32}',binding['attempt']) or
            not isinstance(binding['boot_id'],str) or
            not re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',binding['boot_id'])):
        raise ValueError('Invalid pinned restore stream binding')
    return dict(binding)


def completion(manifest,pin,*,expected_kind=BACKUP):
    return source_completion(manifest, pin, expected_kind=expected_kind)


def frame(kind,binding,**values):
    return dict(type=kind,**binding,**values)


def header(stream):
    line=stream.readline(MAX_HEADER+1)
    if type(line) is not bytes or not line.endswith(b'\n') or len(line)>MAX_HEADER:
        raise ValueError('Missing, truncated or oversized restore frame header')
    return decode(line)


def write_all(output,data):
    view=memoryview(data)
    while view:
        count=output.write(view)
        if type(count) is not int or not 0<count<=len(view):
            raise OSError('Restore transport made invalid or no write progress')
        view=view[count:]


def write_header(output,value):
    data=(json.dumps(value,sort_keys=True,allow_nan=False)+'\n').encode()
    if len(data)>MAX_HEADER: raise ValueError('Restore frame header exceeds bound')
    write_all(output,data)


class Sender:
    """Stream only verified chunks; caller closes stdin after finish().

    Transport errors are terminal for this sender. The outer host transport
    must retain uncertain intent and reconcile, never blindly resend a frame.
    """
    def __init__(self,output,manifest,pin,binding,*,expected_kind=BACKUP):
        validate(manifest, pin, expected_kind=expected_kind)
        self.binding=checked_binding(binding,pin)
        self.manifest=json.loads(json.dumps(manifest))
        self.pin,self.output=pin,output
        self.source_kind=expected_kind
        self.index=0
        self.terminal=False

    def active(self):
        if self.terminal: raise RuntimeError('Restore sender is terminal; reconcile before another attempt')

    def chunk(self,chunk,data):
        self.active()
        try:
            if self.index>=len(self.manifest['chunks']): raise ValueError('Unexpected extra restore chunk')
            expected=self.manifest['chunks'][self.index]
            if (not exact(chunk,expected) or type(data) is not bytes or len(data)!=expected['bytes'] or
                    hashlib.sha256(data).hexdigest()!=expected['sha256']):
                raise ValueError('Unverified restore chunk cannot enter transport')
            write_header(self.output,frame('chunk',self.binding,index=self.index,chunk=expected))
            write_all(self.output,data)
            self.output.flush()
            self.index+=1
        except BaseException:
            self.terminal=True
            raise

    def finish(self,receipt):
        self.active()
        try:
            if self.index!=len(self.manifest['chunks']) or not exact(receipt,completion(
                    self.manifest,self.pin,expected_kind=self.source_kind)):
                raise ValueError('Verified complete source stream required')
            write_header(self.output,frame('source-complete',self.binding,source=receipt))
            self.output.flush()
        finally:
            self.terminal=True


def receive(source,writer,binding,acknowledge,*,progress=None,defer_eof=False,expected_kind=BACKUP):
    """Consume bounded frames, rejecting wrong sessions before device access.

    An EOF is required after the source-complete frame. The caller must provide
    an I/O deadline and independently check the live boot before constructing
    the writer. The returned root-only receipt cannot release recovery hold.
    An installed executor may defer EOF until its protected-range verification
    finishes, keeping the control channel available for lease handshakes. It
    MUST then require EOF before journaling any overall completion receipt.
    Derivatives require explicit owner selection on both receiver and writer;
    client-supplied frames cannot switch the expected source kind.
    """
    binding=checked_binding(binding,writer.pin)
    if (not callable(acknowledge) or progress is not None and not callable(progress) or
            type(defer_eof) is not bool):
        raise ValueError('Expected restore acknowledgement/progress callbacks')
    try:
        validate(writer.manifest, writer.pin, expected_kind=expected_kind)
        if writer.source_kind != expected_kind:
            raise ValueError('Restore receiver and writer source kinds differ')
        writer.active()
        if writer.index!=0: raise ValueError('Restore transport requires a fresh writer')
        for index,chunk in enumerate(writer.manifest['chunks']):
            if not exact(header(source),frame('chunk',binding,index=index,chunk=chunk)):
                raise ValueError('Restore frame differs from pinned session or chunk order')
            remaining=chunk['bytes']
            data=bytearray()
            while remaining:
                part=source.read(min(65536,remaining))
                if type(part) is not bytes or not 0<len(part)<=remaining:
                    raise ValueError('Restore chunk body ended early or exceeded its bound')
                data.extend(part)
                remaining-=len(part)
            result=writer.apply(chunk,bytes(data))
            acknowledge(frame('chunk-verified',binding,index=index,result=result))
        expected=frame('source-complete',binding,source=completion(
            writer.manifest,writer.pin,expected_kind=expected_kind))
        if not exact(header(source),expected): raise ValueError('Missing verified source completion')
        if not defer_eof and source.read(1)!=b'':
            raise ValueError('Unexpected bytes after restore source completion')
        result=writer.finish(progress=progress)
        response=frame('root-verified',binding,result=result)
        if defer_eof: response['input_closed']=False
        return response
    except BaseException:
        writer.failed=True
        raise
