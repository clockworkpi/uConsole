#!/usr/bin/env python3
"""Pi5 boot-device digital MMIO, actual card detect, migration and reset guards."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import tempfile
from test_emulator_pcie_xhci import Machine
from test_emulator_pmic import connect
from test_emulator_adc_migration import migrate
from uconsole_emulator import qmp

CFG=0x1000fff400
PIN=0x107d504100
AON=0x107d510700
GPIO=0x107d517c00
RNG=0x107d208000

class Pi5Machine(Machine):
    def __init__(self,root,name,stack,binary,card=None,incoming=False):
        self.monitor=root/(name+'-qmp');test=root/(name+'-qtest')
        log=stack.enter_context((root/(name+'.log')).open('wb'))
        cmd=[binary,'-M','raspi5b','-m','2G','-accel','qtest','-S','-display','none','-serial','none','-monitor','none','-nic','none',
             '-qtest',f'unix:{test},server=on,wait=off','-qmp',f'unix:{self.monitor},server=on,wait=off']
        if card:cmd+=['-drive',f'if=sd,format=raw,file={card}']
        if incoming:cmd+=['-incoming','defer']
        self.process=subprocess.Popen(cmd,stdout=log,stderr=log);stack.callback(self.close)
        try:self.sock=stack.enter_context(connect(test,self.process))
        except Exception:
            print(Path(log.name).read_text(errors='replace'));raise
        self.stream=stack.enter_context(self.sock.makefile('rwb',buffering=0))

def run(binary):
    with tempfile.TemporaryDirectory(prefix='p5-',dir='/tmp') as temp,ExitStack() as stack:
        root=Path(temp);m=Pi5Machine(root,'source',stack,binary)
        assert m.read(CFG)==0 and m.read(CFG+0x1ac)==0
        m.write(CFG,0xffffffff);assert m.read(CFG)==0xc0000000
        m.write(CFG+0x1ac,0xffffffff);assert m.read(CFG+0x1ac)==0x80000001
        m.write(CFG+4,0x12345678);assert m.read(CFG+4)==0
        assert m.read(GPIO+8)==0x7fff and m.read(GPIO+40)==0x3f
        assert m.read(GPIO+4)&32  # absent real SD backend => CD_N high
        m.write(GPIO+4,0xffffffff);assert m.read(GPIO+4)==0x7fff
        m.write(GPIO+36,0xffffffff);assert m.read(GPIO+36)==0x3f
        m.write(GPIO+0x1c,0xffffffff);assert m.read(GPIO+0x1c)==0
        m.write(PIN+0x18,0x000aa080);m.write(AON+0xc,0x00400000)
        assert m.read(PIN+0x18)==0x000aa080 and m.read(AON+0xc)==0x00400000
        cpus=qmp(m.monitor,'query-cpus-fast')
        assert len(cpus)==4
        for i,cpu in enumerate(cpus):
            v=qmp(m.monitor,'qom-get',{'path':cpu['qom-path'],'property':'mp-affinity'})
            assert v==i<<8
        assert m.read(RNG+0x24)&0xff==0
        m.write(RNG,1);assert m.read(RNG+0x24)&0xff
        assert len({m.read(RNG+0x20) for _ in range(8)})>1
        m.write(RNG,0)
        target=Pi5Machine(root,'target',stack,binary,incoming=True)
        migrate(m,target,root)
        assert target.read(CFG)==0xc0000000
        assert target.read(CFG+0x1ac)==0x80000001
        assert target.read(PIN+0x18)==0x000aa080
        assert target.read(AON+0xc)==0x00400000
        assert target.read(GPIO+4)==0x7fff
        qmp(target.monitor,'system_reset')
        assert target.read(CFG)==0 and target.read(CFG+0x1ac)==0
        assert target.read(PIN+0x18)==0 and target.read(AON+0xc)==0
        assert target.read(GPIO+8)==0x7fff and target.read(GPIO+4)==32
        card=root/'card.raw'
        with card.open('wb') as f:f.truncate(64*1024*1024)
        present=Pi5Machine(root,'present',stack,binary,card=card)
        assert present.read(GPIO+4)&32==0  # same model, actual card attached
        for bad in [['-m','1G'],['-m','2G','-cpu','cortex-a72']]:
            proc=subprocess.run([binary,'-M','raspi5b',*bad,'-display','none','-serial','none','-monitor','none','-nic','none'],capture_output=True,timeout=10)
            assert proc.returncode!=0 and (b'supports 2, 4 or 8' in proc.stderr or b'CPU type' in proc.stderr or b'CPU model' in proc.stderr),proc.stderr
    print('PASS Pi5 MMIO/card/CPU topology/migration/reset/refusal controls')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qemu',required=True);args=p.parse_args();run(str(Path(args.qemu).resolve()))
