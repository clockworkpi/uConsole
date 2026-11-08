#!/usr/bin/env python3
"""Synthetic selected PCIe2 protocol, real translated xHCI DMA and MIP MSI.

This is not a BCM2712/Pi500/RP1 hardware simulator or electrical acceptance.
"""
import argparse
from contextlib import ExitStack
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from test_emulator_pcie_xhci import Machine, rings, noop, acknowledge, geometry
from test_emulator_pmic import connect
from test_emulator_adc_migration import migrate
from uconsole_emulator import executable, qmp
from test_emulator_pi4_memory import sparse_ram

PCIE=0x1000120000
MIP=0x1000130000
RESCAL=0x1000119500
RESET=0x1001504318
GPIO=0x107d517c00
PINS=0x107d510700
GIC=0xff841000
CPU=0xff842000


class ContractMachine(Machine):
    def __init__(self,root,name,stack,binary,incoming=False,accel='qtest'):
        self.monitor=root/(name+'-qmp');test=root/(name+'-qtest')
        log=stack.enter_context((root/(name+'.log')).open('wb'))
        self.process=subprocess.Popen([binary,'-M','raspi4b-pcie2-contract,memory-backend=test-memory','-global',
            'bcm2838-peripherals.pcie2-contract=on','-accel',accel,'-S',
            *sparse_ram(8),'-display','none','-serial','none','-monitor','none','-nic','none',
            '-qtest',f'unix:{test},server=on,wait=off',
            '-qmp',f'unix:{self.monitor},server=on,wait=off',
            *(['-incoming','defer'] if incoming else [])],stdout=log,stderr=log)
        stack.callback(self.close)
        try:self.sock=stack.enter_context(connect(test,self.process))
        except Exception:
            print(Path(log.name).read_text(errors='replace')[-4096:]);raise
        self.stream=stack.enter_context(self.sock.makefile('rwb',buffering=0))
        self.xbase=0x1f00000000
        self.ram_offset=0x100000000  # actual high physical source pages
        self.dma_base=0x1000000000+self.ram_offset
        self.generic=False
        self.current_data=0

    def reg(self,offset,value=None):
        if value is None:return self.read(PCIE+offset)
        self.write(PCIE+offset,value)

    def config(self,bus,dev,fn,offset,value=None):
        assert 0<=offset<=0xffc and offset%4==0
        address=PCIE+offset
        if (bus,dev,fn)!=(0,0,0):
            self.reg(0x9000,(bus<<20)|(dev<<15)|(fn<<12));address=PCIE+0x8000+offset
        if value is None:return self.read(address)
        self.write(address,value)

    def irq(self):
        irq=160+self.current_data
        return bool(self.read(GIC+0x200+(irq//32)*4)&(1<<(irq%32)))


def setup(m):
    # Actual STB provider protocol and reset44 bank/bit.
    assert m.read(RESET+0x20)&0x1000
    m.write(RESET+0x1c,0x1000)
    assert not m.read(RESET+0x20)&0x1000
    assert not m.read(RESCAL)&1
    m.write(RESCAL,1);assert m.read(RESCAL)&1 and m.read(RESCAL+8)&1
    m.write(RESCAL,0);assert not m.read(RESCAL)&1
    m.write(RESET+0x18,0x1000);assert m.read(RESET+0x20)&0x1000
    m.write(RESET+0x1c,0x1000)
    m.reg(0x4064,0);m.reg(0x4304,0)
    assert m.reg(0x4068)&0x80 and not m.reg(0x4068)&0x30
    # PRESET data precedes GPIO mux selection/direction. Readback is real state.
    m.write(GPIO+4,4);m.write(PINS+0xc,0);m.write(GPIO+8,0x7ffb)
    assert m.read(GPIO+4)==4 and m.read(GPIO+8)==0x7ffb
    for reg,data in [(0x1f,0x1600),(0x16,0x50b9),(0x17,0xbda1),(0x18,0x94),
                     (0x19,0x97b4),(0x1b,0x5030),(0x1c,0x5030),(0x1e,7)]:
        m.reg(0x1100,reg);m.reg(0x1104,0x80000000|data)
        assert not m.reg(0x1104)&0x80000000
    m.reg(0x4008,0x203480)
    # Presealed400b outbound two tuples. Unused windows remain disabled.
    for i,(high,last,pcihi) in enumerate([(0x1f,0x1f,0),(0x1c,0x1e,4)]):
        for off,val in [(0x400c+i*8,0),(0x4010+i*8,pcihi),
                        (0x4070+i*4,0xfff00000),(0x4080+i*8,high),(0x4084+i*8,last)]:m.reg(off,val)
    # Three routes: peer, real RAM64GiB aperture, raw MIP. No RAM offset on MSI.
    for i,(plo,phi,clo,chi) in enumerate([(7,0,1,0x1f),(0x15,0x10,1,0),(0xfffff01c,0xff,0x130001,0x10)]):
        for off,val in [(0x402c+i*8,plo),(0x4030+i*8,phi),(0x40ac+i*8,clo),(0x40b0+i*8,chi)]:m.reg(off,val)
    for i in range(3,10):assert m.reg(0x40d4+(i-3)*8)&31==0
    # Exact native phase5 update: generic TLS is initialized from x-speed.
    # This must fail on old2dbf (fixed genericTLS4), not be skipped by a manual init.
    cap=m.reg(0xb8)
    assert cap&15==2 and (cap>>4)&0x3f==4,hex(cap)
    value=m.reg(0x4dc);m.reg(0x4dc,(value&~(0xc00|0x1ff))|0x842)
    before=m.read(PCIE+0xdc,'w')
    m.write(PCIE+0xdc,(before&~15)|2,'w')
    assert m.read(PCIE+0xdc,'w')&15==2
    # The selected QEMU profile does not claim general retraining/TLS writability.
    m.write(PCIE+0xdc,3,'w');assert m.read(PCIE+0xdc,'w')&15==2
    m.reg(0x4064,4);assert m.reg(0x4068)&0x30==0x30
    # Actual QEMU IDs/capabilities, not an RP1 identity.
    assert m.config(0,0,0,0)==0x000c1b36
    m.config(0,0,0,0x18,0x00010100)
    m.config(0,0,0,0x20,0) # exact native bridge window0..0xfffff
    m.config(0,0,0,4,6)
    assert m.config(1,0,0,8)>>8==0x0c0330
    m.config(1,0,0,0x10,0xffffffff)
    assert m.config(1,0,0,0x10)&~15==0xffffc000
    m.config(1,0,0,0x10,0);m.config(1,0,0,0x14,0);m.config(1,0,0,4,6)
    assert m.xread(0)==0x01000040
    assert m.read(m.xbase,'b')==0x40 # native CAPLENGTH at legalPCI0
    geometry(m,compact=False)
    for bdf in [(1,1,0),(1,0,1),(2,0,0)]:assert m.config(*bdf,0)==0xffffffff
    # Actual MIP edge/host/VPU configuration from selected provider.
    for off in [0x20,0x30,0x60,0x70]:m.write(MIP+off,0xffffffff)
    for off in [0x40,0x50]:m.write(MIP+off,0)
    # Reserve/enable native INTID160, edge-triggered, targetCPU0.
    assert m.read(GIC+4)&31>=8  # expanded distributor >=288interrupts
    m.write(GIC,1)
    for bank in [5,6]:m.write(GIC+0x100+bank*4,0xffffffff)
    for off in range(0x8a0,0x8e0,4):m.write(GIC+off,0x01010101)
    for off in range(0xc28,0xc38,4):m.write(GIC+off,0xaaaaaaaa)
    m.write(CPU,1);m.write(CPU+4,0xff)
    return capabilities(m)


def capabilities(m):
    found={};cap=m.config(1,0,0,0x34)&255;seen=set()
    while cap:
        assert 0x40<=cap<=0xfc and cap%4==0 and cap not in seen
        seen.add(cap);word=m.config(1,0,0,cap);found[word&255]=cap;cap=(word>>8)&255
    assert 5 in found and 0x11 in found
    return found


def msi(m,caps,data=0):
    m.current_data=data
    cap=caps[5];header=m.config(1,0,0,cap);assert header&(1<<23) #64-bit message
    m.config(1,0,0,cap+4,0xfffff000);m.config(1,0,0,cap+8,0xff)
    m.config(1,0,0,cap+12,data)
    m.config(1,0,0,cap,header|(1<<16))


def eoi(m):
    value=m.read(CPU+0xc);assert value&0x3ff==160+m.current_data,hex(value)
    m.write(CPU+0x10,value);assert not m.irq()


def run(binary,root):
    with ExitStack() as stack:
        m=ContractMachine(root,'source',stack,binary);caps=setup(m)
        # All64 actual endpoint MSI messages: data is an index, not a bitmap.
        for data in range(64):
            msi(m,caps,data);rings(m);noop(m,0);eoi(m);acknowledge(m,1)
        # Real MSI-X table path, separate from the ordinary MSI capability.
        cap=caps[5];m.config(1,0,0,cap,m.config(1,0,0,cap)&~(1<<16))
        cap=caps[0x11];table=m.config(1,0,0,cap+4)
        assert table&7==0 and (table&~7)+16<=0x4000
        table&=~7
        for off,value in [(0,0xfffff000),(4,0xff),(8,63),(12,0)]:m.xwrite(table+off,value)
        m.config(1,0,0,cap,(m.config(1,0,0,cap)|(1<<31))&~(1<<30))
        m.current_data=63;rings(m);noop(m,0);eoi(m);acknowledge(m,1)
        m.config(1,0,0,cap,m.config(1,0,0,cap)&~(1<<31))
        msi(m,caps);rings(m);noop(m,0);eoi(m);acknowledge(m,1)
        # Masking MIP blocks genuine next xHCI message while event DMA completes.
        m.write(MIP+0x40,1)
        m.memory(0x01000010,struct.pack('<QII',0,0,(23<<10)|1));m.xwrite(m.doorbell,0)
        pointer,status,control=struct.unpack('<QII',m.inspect(0x01200010,16))
        assert pointer==m.dma_base+0x01000010 and status>>24==1 and not m.irq()
        acknowledge(m,2);m.write(MIP+0x40,0)
        noop(m,2) # pending real MSI and event migrate, then complete another command
        target=ContractMachine(root,'target',stack,binary,incoming=True)
        migrate(m,target,root)
        target.oper=m.oper;target.runtime=m.runtime;target.doorbell=m.doorbell
        assert target.irq();eoi(target);acknowledge(target,3);noop(target,3);eoi(target);acknowledge(target,4)
        # GPIO RUN/bridge/PERST gates must stop real MMIO and DMA.
        target.write(GPIO+4,0);assert not target.reg(0x4068)&0x30
        assert target.xread(0)!=0x01000040
        target.write(GPIO+4,4);assert target.xread(0)==0x01000040
        target.write(RESET+0x18,0x1000);assert not target.reg(0x4068)&0x30
        assert target.config(1,0,0,0)==0xffffffff
        qmp(target.monitor,'system_reset')
        assert target.read(RESET+0x20)&0x1000 and target.read(MIP+0x40)==0xffffffff
        print('Synthetic PCIe2: selected reset/AON/MDIO/windows; real high-CPU translated xHCI event DMA, raw-route MSI/GIC, masking, migration continuation and reset PASS')


def cpu_fault_contract(binary):
    # Original AArch64 EL3 payload, assembled once and embedded to avoid a
    # cross-assembler dependency in packaged macOS/Linux tests:
    # mov x1,#0x800; msr vbar_el3,x1; isb; mov x0,#offset;
    # movk x0,#0xfd58,lsl#16; ldr w2,[x0]; mov x3,#0xdead;
    # mov x5,#0x4000; str x3,[x5]; wfi; b .-4
    # Vectors at0x800/current-SP0 and0xa00/current-SPx branch to0x1000:
    # mrs x3,esr_el3; mrs x4,far_el3; mov x5,#0x4000;
    # str x4,[x5,#8]; str x3,[x5]; wfi; b .-4
    for offset, fault, half in ((0x20,False,False),(4,True,False),(0x20,True,True)):
        with tempfile.TemporaryDirectory(prefix='uc-pcie2-cpu-') as temporary:
            root = Path(temporary)
            with (root / 'qemu.log').open('wb') as log:
                process = subprocess.Popen([
                    binary, '-M', 'raspi4b-pcie2-contract,memory-backend=test-memory', *sparse_ram(8), '-global', 'bcm2838-peripherals.pcie2-contract=on', '-accel', 'tcg', '-S',
                    '-display', 'none', '-serial', 'none', '-monitor', 'none',
                    '-qtest', f'unix:{root}/test,server=on,wait=off',
                    '-qmp', f'unix:{root}/qmp,server=on,wait=off',
                ], stdout=log, stderr=log)
                try:
                    with connect(root / 'test', process) as sock:
                        stream = sock.makefile('rwb', buffering=0)

                        def command(text):
                            sock.sendall((text + '\n').encode())
                            while True:
                                result = stream.readline().decode().strip()
                                if result.startswith('IRQ '):
                                    continue
                                assert result.startswith('OK'), result
                                return result

                        program = bytearray(0x1020)
                        main = bytearray.fromhex('010081d201c01ed5df3f03d5000083d200abbff2020040b9a3d59bd2050088d2a30000f97f2003d5ffffff17')
                        # CPU address0x1000130000, not a truncated32-bit alias.
                        main[16:20]=(0xf2a00000|(0x13<<5)).to_bytes(4,'little')
                        main[20:20]=(0xf2c00000|(0x10<<5)).to_bytes(4,'little')
                        if half:main[24:28]=(0x79400002).to_bytes(4,'little')
                        program[:len(main)] = main
                        program[12:16] = (0xd2800000 | (offset << 5)).to_bytes(4, 'little')
                        program[0x800:0x804] = bytes.fromhex('00020014')
                        program[0xa00:0xa04] = bytes.fromhex('80010014')
                        handler = bytes.fromhex('03523ed504603ed5050088d2a40400f9a30000f97f2003d5ffffff17')
                        program[0x1000:0x1000 + len(handler)] = handler
                        command(f'write 0 {len(program):#x} 0x{program.hex()}')
                        qmp(root / 'qmp', 'cont')
                        deadline = time.monotonic() + 10
                        while True:
                            esr = int(command('readq 0x4000').split()[1], 16)
                            if esr:
                                break
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise TimeoutError('AArch64 MMIO probe did not report: ' + (root / 'qemu.log').read_text())
                            time.sleep(0.01)
                        far = int(command('readq 0x4008').split()[1], 16)
                        if fault:
                            # EA bit9 encodes the external-abort type and can differ
                            # between EL3 and the firmware EL2 exception route.
                            assert esr & ~0x200 == 0x96000010 and far == MIP + offset, (hex(esr), hex(far))
                        else:
                            assert esr == 0xdead, (hex(esr), hex(far))
                    qmp(root / 'qmp', 'quit')
                    assert process.wait(timeout=10) == 0
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)
        print('PASS: actual AArch64 CPU synthetic MIP ' + ('unsupported MMIO external abort' if fault else 'mapped MMIO control'))


def zero_bar_storage_contract(binary, output):
    # Reuse the existing real BOT/SCSI driver, with MIP edge acknowledgement.
    import test_emulator_xhci_storage as storage
    root = output / 'zero-bar-storage'
    root.mkdir()
    pattern = bytes((i * 17 + i // 4096) & 255 for i in range(256 * 1024))
    disk = root / 'disk.raw'
    disk.write_bytes(pattern)
    original_ack = storage.acknowledge
    delivered = []
    def edge_ack(machine, event):
        if machine.irq():
            eoi(machine)
            delivered.append(event)
        # ERDP acknowledgement may immediately generate a new MSI for the
        # following event. The reusable helper's INTx-low assertion is not
        # applicable to an edge interrupt already latched in the GIC.
        intr = machine.runtime + 0x20
        pointer = machine.dma_base + 0x01200000 + event * 16
        machine.xwrite(intr + 0x18, (pointer | 8) & 0xffffffff)
        machine.xwrite(intr + 0x1c, pointer >> 32)
        machine.xwrite(intr, 3)
        machine.xwrite(machine.oper + 4, 8)
        if machine.irq():
            eoi(machine)
            delivered.append(event)
    try:
        storage.acknowledge = edge_ack
        with ExitStack() as stack:
            m = ContractMachine(root, 'storage', stack, binary)
            caps = setup(m)
            msi(m, caps)
            qmp(m.monitor, 'blockdev-add', {'node-name': 'disk', 'driver': 'raw',
                'file': {'driver': 'file', 'filename': str(disk.resolve())}})
            qmp(m.monitor, 'device_add', {'driver': 'usb-storage', 'id': 'stick',
                'bus': 'pcie2-xhci.0', 'drive': 'disk'})
            rings(m)
            device = storage.Storage(m)
            device.setup()
            device.read(512)
            assert device.csw()[12] == 1
            for size in (65536, 131072):
                _, events = device.read(size)
                assert m.inspect(storage.DATA, size) == pattern[:size]
                assert len(events) == size // 65536 and all(e[1] == 1 for e in events), events
                assert device.csw()[12] == 0
            assert disk.read_bytes() == pattern and len(delivered) >= 8
            print('PASS: real USB BOT/SCSI64/128KiB payload DMA and MIP MSI through zeroPCI BAR')
    finally:
        storage.acknowledge = original_ack


def zero_bar_cpu_contract(binary,output):
    root=output/'zero-bar-cpu';root.mkdir()
    with ExitStack() as stack:
        m=ContractMachine(root,'cpu',stack,binary,accel='tcg');setup(m)
        assert m.config(1,0,0,0x10)&~15==0 and m.config(0,0,0,0x20)==0
        program=bytearray(0x1020)
        main=bytearray.fromhex('010081d201c01ed5df3f03d5000083d200abbff2020040b9a3d59bd2050088d2a30000f97f2003d5ffffff17')
        main[12:16]=(0xd2800000).to_bytes(4,'little')
        main[16:20]=(0xf2a00000).to_bytes(4,'little')
        main[20:20]=(0xf2c00000|(0x1f<<5)).to_bytes(4,'little')
        main[24:28]=(0x39400002).to_bytes(4,'little') # ldrb w2,[x0]
        main[36:36]=(0xf90008a2).to_bytes(4,'little') # str x2,[x5,#16]
        program[:len(main)]=main
        program[0x800:0x804]=bytes.fromhex('00020014')
        program[0xa00:0xa04]=bytes.fromhex('80010014')
        handler=bytes.fromhex('03523ed504603ed5050088d2a40400f9a30000f97f2003d5ffffff17')
        program[0x1000:0x1000+len(handler)]=handler
        m.command(f'write 0 {len(program):#x} 0x{program.hex()}');qmp(m.monitor,'cont')
        deadline=time.monotonic()+10
        while True:
            status=m.read(0x4000,'q')
            if status:break
            if time.monotonic()>deadline:raise TimeoutError('ActualCPU zeroBAR readb witness timeout')
            time.sleep(.01)
        value=m.read(0x4010,'q');far=m.read(0x4008,'q')
        assert status==0xdead and value==0x40,(hex(status),hex(far),hex(value))
        import hashlib,json
        result={'passed':True,'qemu_sha256':hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                'actual_cpu_load':'LDRB','pci_bar_base':0,'cpu_address':'0x1f00000000',
                'bridge_window':'0..0xfffff','cpu_value':value,'success_marker':status,
                'scope':'ActualAArch64 byte read via outbound/bridge/real xHCI BAR atPCI0'}
        (root/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        qmp(m.monitor,'quit');assert m.process.wait(timeout=10)==0
        print('PASS: actual AArch64 LDRB CAPLENGTH40 through legal zeroPCI BAR and native bridge window')


def main():
    p=argparse.ArgumentParser();p.add_argument('--qemu',default='qemu-system-aarch64');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.output:
        a.output.mkdir(parents=True,exist_ok=False);run(executable(a.qemu),a.output);zero_bar_storage_contract(executable(a.qemu),a.output);zero_bar_cpu_contract(executable(a.qemu),a.output);cpu_fault_contract(executable(a.qemu))
    else:
        with tempfile.TemporaryDirectory(prefix='pcie2-contract-') as d:run(executable(a.qemu),Path(d));zero_bar_storage_contract(executable(a.qemu),Path(d));zero_bar_cpu_contract(executable(a.qemu),Path(d));cpu_fault_contract(executable(a.qemu))
if __name__=='__main__':main()
