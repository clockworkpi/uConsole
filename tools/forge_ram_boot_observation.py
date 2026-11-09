"""Read physical firmware-selection evidence without requiring a disk root."""
import shlex

from forge_ram_identity import READER, verify


SOURCE = r'''
import contextlib,io,json
from pathlib import Path
output=io.StringIO()
with contextlib.redirect_stdout(output): exec(identity_reader,{})
identity=json.loads(output.getvalue())
directory=Path('/proc/device-tree/chosen/bootloader')
def cell(name):
    with (directory/name).open('rb') as stream: value=stream.read(5)
    if len(value)!=4: raise ValueError('Expected one bootloader device-tree cell')
    return int.from_bytes(value,'big')
bootloader=dict(tryboot=cell('tryboot'),partition=cell('partition'))
if Path('/proc/sys/kernel/random/boot_id').read_text().strip()!=identity['boot_id']:
    raise ValueError('RAM boot changed during firmware observation')
print(json.dumps(dict(identity=identity,bootloader=bootloader)))
'''


def checked(record, nonce, kernel, serial, *, boot_id, expected_tryboot, expected_partition=1):
    if (type(expected_tryboot) is not int or expected_tryboot not in (0,1) or
            type(expected_partition) is not int or expected_partition!=1):
        raise ValueError('Expected explicit CM4 boot-partition selection')
    if not isinstance(record,dict) or set(record)!={'identity','bootloader'}:
        raise ValueError('Unexpected physical RAM firmware observation')
    identity=verify(record['identity'],nonce,kernel,serial,mode='physical')
    if identity['boot_id']!=boot_id: raise ValueError('Physical RAM boot UUID differs')
    loader=record['bootloader']
    if (not isinstance(loader,dict) or set(loader)!={'tryboot','partition'} or
            type(loader['tryboot']) is not int or loader['tryboot']!=expected_tryboot or
            type(loader['partition']) is not int or loader['partition']!=expected_partition):
        raise ValueError('Physical firmware boot selection differs')
    return dict(status='verified-physical-ram-selection',boot_id=boot_id,nonce=nonce,
                selection='tryboot' if expected_tryboot else 'normal',partition=expected_partition,
                root_write_authorized=False,normal_boot_release_authorized=False)


def capture(probe, *, boot_id, expected_tryboot, expected_partition=1):
    if (probe.mode!='physical' or type(expected_tryboot) is not int or expected_tryboot not in (0,1) or
            type(expected_partition) is not int or expected_partition!=1):
        raise ValueError('Physical firmware observation requires explicit supported selection')
    source='identity_reader='+repr(READER)+'\n'+SOURCE
    observed=probe._observe('/usr/bin/python3 -I -S -c '+shlex.quote(source))
    result=checked(observed,probe.nonce,probe.kernel,probe.serial,boot_id=boot_id,
                   expected_tryboot=expected_tryboot,expected_partition=expected_partition)
    return dict(observation=observed,verification=result)
