#!/usr/bin/env python3
"""Prepare explicit Pi5 SD-only model FDT and FreeBSD Linux-boot-ABI Image.

Development boot inputs, not a physical firmware image. No driver code is
patched. Every derived FDT change is recorded for review beside the output.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from compose_pcie2_contract_dtb import decode, encode

def digest(data): return hashlib.sha256(data).hexdigest()

def kernel_image(data):
    if len(data)<64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise ValueError('expected little-endian AArch64 ELF')
    entry, phoff = struct.unpack_from('<QQ', data, 24)
    entsize, count = struct.unpack_from('<HH', data, 54)
    if entsize != 56 or not 0 < count <= 128 or phoff+count*entsize > len(data):
        raise ValueError('invalid ELF program table')
    segments = [struct.unpack_from('<IIQQQQQQ', data, phoff+i*entsize)
                for i in range(count)]
    segments = [s for s in segments if s[0] == 1]
    base = min(s[3] for s in segments)
    if not segments:raise ValueError('ELF has no load segments')
    if base != 0xffff000000000000:raise ValueError('expected FreeBSD arm64 kernel virtual base')
    size = max(s[3]-base+s[6] for s in segments)
    if not 0 < size <= 256*1024*1024 or not base <= entry < base+size:
        raise ValueError('unreasonable kernel layout')
    out = bytearray((size+0xffff)&~0xffff)
    occupied=[]
    for _, flags, off, va, pa, filesz, memsz, align in segments:
        if pa!=va or any(va<a+length and a<va+memsz for a,length in occupied):
            raise ValueError('unsupported or overlapping load layout')
        if flags&1 and va-base<64 and memsz:raise ValueError('executable ELF header would be overwritten')
        occupied.append((va,memsz))
        if filesz > memsz or off+filesz > len(data): raise ValueError('short segment')
        if va < base or va-base+filesz > len(out): raise ValueError('segment bounds')
        out[va-base:va-base+filesz] = data[off:off+filesz]
    offset = entry-base
    if offset % 4 or offset >= 128*1024*1024: raise ValueError('entry branch range')
    # Existing FreeBSD LINUX_BOOT_ABI accepts the unmodified FDT in x0.
    # Replace the unused ELF file header with the standard AArch64 Image header.
    struct.pack_into('<IIQQQQQQII',out,0,0x14000000+(offset//4),0,
                     0x200000,len(out),0,0,0,0,0x644d5241,0)
    return bytes(out)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dtb',type=Path,required=True)
    p.add_argument('--kernel',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--memory-gib', type=int, choices=[2,4,8], default=8)
    args=p.parse_args()
    raw=args.dtb.read_bytes();n,reserve,boot=decode(raw)
    if b'brcm,bcm2712' not in n['/'].get('compatible',b'').split(b'\0'):
        raise ValueError('expected a BCM2712 tree')
    if n['/'].get('#address-cells')!=struct.pack('>I',2) or n['/'].get('#size-cells')!=struct.pack('>I',1):
        raise ValueError('unsupported BCM2712 root address/size cells')
    before={k:dict(v) for k,v in n.items()}
    soc='/soc@107c000000'
    devices={soc+'/mmc@fff000',soc+'/serial@7d001000',
             soc+'/interrupt-controller@7fff9000',soc+'/gpio@7d517c00',
             soc+'/rng@7d208000',
             soc+'/pinctrl@7d504100',soc+'/pinctrl@7d510700',
             '/timer','/psci','/sd-io-1v8-reg','/sd-vcc-reg'}
    # Unsupported BCM2712/RP1 devices remain visible but explicitly disabled.
    # Preserve the actual CPU, bus, SD provider/regulator, GIC and UART cells.
    for path,props in n.items():
        if 'compatible' not in props or path in devices:continue
        if path.startswith('/cpus') or path in ['/','/axi',soc] or path in ['/clocks/clk-emmc2','/clocks/clk-uart','/clocks/clk-vpu']:continue
        props['status']=b'disabled\0'
    for path in devices:n[path]['status']=b'okay\0'
    # Explicit firmware-equivalent RAM sizing and initialized debug UART.
    for path,props in n.items():
        if props.get('device_type')==b'memory\0':
            props['reg']=b''.join(struct.pack('>III',base >> 32,base & 0xffffffff,0x80000000)
                                  for base in range(0,args.memory_gib*1024**3,0x80000000))
    n['/chosen']['stdout-path']=(soc+'/serial@7d001000:115200n8\0').encode()
    n['/chosen']['bootargs']=b'FreeBSD: vfs.root.mountfrom=ufs:/dev/gpt/PI5ROOT boot_verbose=YES kern.panic_reboot_wait_time=-1\0'
    n[soc+'/serial@7d001000'].pop('skip-init',None)
    # The raw firmware tree includes unused clock placeholders. FreeBSD's
    # fixed-clock probe does not honor status=disabled. Omit these unmodeled
    # nodes, as QEMU's other board DT fixups do for unsupported devices.
    removed=[path for path,props in n.items() if
             (path.startswith('/clocks/') or
              b'fixed-clock' in props.get('compatible',b'').split(b'\0') or
              b'fixed-factor-clock' in props.get('compatible',b'').split(b'\0')) and
             path not in ['/clocks/clk-emmc2','/clocks/clk-uart','/clocks/clk-vpu']]
    for path in removed:del n[path]
    if '/__symbols__' in n:
        for key,value in list(n['/__symbols__'].items()):
            if value.rstrip(b'\0').decode() in removed:del n['/__symbols__'][key]

    derived=encode(n,reserve,boot);image=kernel_image(args.kernel.read_bytes())
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'pi5-sd.dtb').write_bytes(derived)
    (args.output/'FreeBSD-Image').write_bytes(image)
    changes=[]
    for path in n:
        for key in n[path].keys()|before[path].keys():
            a,b=before[path].get(key),n[path].get(key)
            if a!=b:changes.append({'path':path,'property':key,'before':None if a is None else a.hex(),'after':None if b is None else b.hex()})
    (args.output/'inputs.json').write_text(json.dumps({'source_dtb_sha256':digest(raw),'source_kernel_sha256':digest(args.kernel.read_bytes()),'derived_dtb_sha256':digest(derived),'image_sha256':digest(image),'changes':changes,'removed_unmodeled_clock_nodes':removed,'memory_gib':args.memory_gib,'scope':'Pi5 SD-only model; no VideoCore or RP1 qualification'},indent=2)+'\n')
if __name__=='__main__':main()
