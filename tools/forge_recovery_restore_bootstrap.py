"""Pinned, RAM-only owner-code bootstrap for the physical restore worker.

Sources come only from installed sibling modules, never guest/client paths.
The packet is bounded and hashed before loading any owner code. The SSH command
pins that packet independently of its input bytes. This builds commands only;
durable dispatch and owner/source-health approval remain separate requirements.
"""
import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct

from forge_ram_transport import RecoveryProbe
from forge_recovery_commit_protocol import exact
from forge_recovery_restore_ledger import request as check_request
from forge_recovery_operation_contract import check_evidence
from forge_recovery_restore_protocol import header, write_all

MAX_PACKET = 100*1024*1024

BOOTSTRAP = r'''import hashlib,importlib.abc,importlib.util,json,os,re,select,struct,sys,time
expected=sys.argv[1]
if not re.fullmatch('[0-9a-f]{64}',expected): raise ValueError('Invalid owner packet pin')
deadline=time.monotonic()+60
def read_exact(length):
    result=bytearray()
    while len(result)<length:
        remaining=deadline-time.monotonic()
        if remaining<=0 or not select.select([0],[],[],remaining)[0]:
            raise TimeoutError('Owner bootstrap input deadline')
        data=os.read(0,min(65536,length-len(result)))
        if not data: raise EOFError('Incomplete owner bootstrap packet')
        result.extend(data)
    return bytes(result)
length=struct.unpack('!Q',read_exact(8))[0]
if not 0<length<=100*1024*1024: raise ValueError('Owner packet exceeds bound')
data=read_exact(length)
if hashlib.sha256(data).hexdigest()!=expected: raise ValueError('Owner packet pin differs')
def unique(items):
    value={}
    for key,item in items:
        if key in value: raise ValueError('Duplicate owner packet field')
        value[key]=item
    return value
packet=json.loads(data,object_pairs_hook=unique,
    parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite owner packet value')))
if (not isinstance(packet,dict) or set(packet)!={'schema','modules','request'} or
        type(packet['schema']) is not int or packet['schema']!=1):
    raise ValueError('Invalid owner packet schema')
modules=packet['modules']
if (not isinstance(modules,dict) or not 1<=len(modules)<=128 or
        not {'forge_recovery_restore_worker','forge_recovery_restore_io'}<=set(modules) or
        any(not re.fullmatch('forge_[a-z0-9_]+',name) or not isinstance(source,str) or
            not 0<len(source.encode())<=2*1024*1024 for name,source in modules.items())):
    raise ValueError('Invalid bounded owner module set')
class Loader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
    def find_spec(self,fullname,path=None,target=None):
        if fullname in modules: return importlib.util.spec_from_loader(fullname,self)
        if fullname.startswith('forge_'): raise ModuleNotFoundError('Missing pinned owner module: '+fullname)
    def create_module(self,spec): return None
    def exec_module(self,module):
        name=module.__name__
        module.__file__='/run/forge-owner-code/'+name+'.py'
        exec(compile(modules[name],module.__file__,'exec'),module.__dict__)
loader=Loader()
sys.meta_path.insert(0,loader)
try:
    from forge_recovery_restore_io import PipeIO
    from forge_recovery_restore_worker import run
    with PipeIO(0,1) as channel:
        value=(json.dumps(dict(type='bootstrap-ready',packet_sha256=expected))+'\n').encode()
        offset=0
        while offset<len(value): offset+=channel.write(value[offset:])
        channel.flush()
        run(packet['request'],channel)
finally:
    sys.meta_path.remove(loader)
'''


OBSERVATION_BOOTSTRAP = BOOTSTRAP.replace(
    'from forge_recovery_restore_worker import run',
    'from forge_recovery_restore_observe_worker import run')


def sources(*, observation=False):
    """Freeze the installed transitive local import closure, including lazy imports."""
    directory = Path(__file__).resolve().parent
    result = {}
    def visit(name):
        if name in result: return
        if not re.fullmatch('forge_[a-z0-9_]+', name):
            raise ValueError('Unexpected local restore worker dependency')
        source = (directory/(name+'.py')).read_text()
        if not 0 < len(source.encode()) <= 2*1024*1024 or len(result) >= 128:
            raise ValueError('Restore owner code closure exceeds bound')
        result[name] = source
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import): names = [value.name for value in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module: names = [node.module]
            for dependency in names:
                if (directory/(dependency+'.py')).is_file(): visit(dependency)
    visit('forge_recovery_restore_worker')
    visit('forge_recovery_restore_io')
    if observation: visit('forge_recovery_restore_observe_worker')
    return result


def payload(plan, pin, inputs, attempt):
    plan, inputs = copy.deepcopy((plan, inputs))
    check_request(plan, pin, attempt)
    check_evidence(plan, pin, inputs)
    value = dict(schema=1, modules=sources(), request=dict(protocol=1, plan=plan, pin=pin,
                                                        inputs=inputs, attempt=attempt))
    data = json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    if not 0 < len(data) <= MAX_PACKET: raise ValueError('Restore owner packet exceeds bound')
    return data, hashlib.sha256(data).hexdigest()


def framed(data, pin):
    if (type(data) is not bytes or not 0 < len(data) <= MAX_PACKET or
            not isinstance(pin, str) or not re.fullmatch('[0-9a-f]{64}', pin) or
            hashlib.sha256(data).hexdigest() != pin):
        raise ValueError('Restore bootstrap bytes differ from owner pin')
    return struct.pack('!Q', len(data))+data


def argv(probe, packet_pin, *, observation=False):
    if (not isinstance(probe, RecoveryProbe) or probe.mode != 'physical' or
            not isinstance(packet_pin, str) or not re.fullmatch('[0-9a-f]{64}', packet_pin)):
        raise ValueError('Expected pinned physical restore bootstrap')
    bootstrap = OBSERVATION_BOOTSTRAP if observation else BOOTSTRAP
    return probe._argv('/usr/bin/python3 -I -S -c '+shlex.quote(bootstrap)+' '+packet_pin)


def observation_payload(request):
    from forge_recovery_restore_observe_worker import checked
    value = dict(schema=1, modules=sources(observation=True), request=checked(request))
    data = json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()
    if not 0 < len(data) <= MAX_PACKET:
        raise ValueError('Restore observation packet exceeds bound')
    return data, hashlib.sha256(data).hexdigest()


def start(channel, data, pin):
    """Load the pinned worker; readiness proves code load, not target identity."""
    write_all(channel, framed(data,pin))
    channel.flush()
    if not exact(header(channel),dict(type='bootstrap-ready',packet_sha256=pin)):
        raise ValueError('Restore worker bootstrap readiness differs from owner packet')
