#!/usr/bin/env python3
"""Hermetic phandle/closure and boot-substrate controls, not native acceptance."""
import hashlib
import unittest
import compose_pcie2_contract_dtb as c


def text(s):return s.encode()+b'\0'

def tree():
    return {'/':{'compatible':text('brcm,bcm2711'),'#address-cells':c.packed([2]),'#size-cells':c.packed([2])},
            '/chosen':{'stdout-path':text('/soc/uart@1000:115200n8'),'phandle':c.packed([0x52])},
            '/soc':{'compatible':text('simple-bus'),'#address-cells':c.packed([1]),'#size-cells':c.packed([1]),'ranges':c.packed([0,0,0xfe000000,0x1000000])},
            '/soc/gic':{'compatible':text('arm,gic-400'),'phandle':c.packed([5]),'#interrupt-cells':c.packed([3]),'interrupt-controller':b''},
            '/soc/clock':{'phandle':c.packed([0x23]),'#clock-cells':c.packed([1])},
            '/soc/uart@1000':{'reg':c.packed([0x1000,0x100]),'clocks':c.packed([0x23,2]),'interrupt-parent':c.packed([5]),'interrupts':c.packed([0,121,4])},
            '/soc/ethernet':{'compatible':text('brcm,bcm2711-genet-v5'),'status':text('okay')},
            '/soc/pcie':{'compatible':text('brcm,bcm2711-pcie'),'status':text('okay')}}


def selected():
    # Minimal parser corpus, deliberately distinct from enrolled400b admission.
    n={'/':{},'/soc@107c000000':{'#address-cells':c.packed([1]),'#size-cells':c.packed([1]),'ranges':c.packed([0,0x10,0,0x80000000])},'/axi':{'#address-cells':c.packed([2]),'#size-cells':c.packed([2])}}
    for name,ph in [('reset-controller@119500',0x23),('reset-controller@1504318',0x24),('pinctrl@7d510700',0x92),('gpio@7d517c00',0x93)]:n['/soc@107c000000/'+name]={'phandle':c.packed([ph])}
    n[c.HOST]={'phandle':c.packed([0xa5]),'msi-parent':c.packed([0x27]),'interrupt-parent':c.packed([1]),'ranges':c.packed([0x2000000,0,0,0x1f,0,0,0xfffffffc])}
    n[c.HOST+'/rp1']={'compatible':text('simple-bus'),'phandle':c.packed([0x28]),'interrupt-parent':c.packed([0x28])}
    n[c.HOST+'/rp1/device']={'compatible':text('rp1-test-peripheral')}
    n['/axi/msi-controller@1000130000']={'phandle':c.packed([0x27]),'msi-ranges':c.packed([1,0,128,1,64])}
    return n


class Controls(unittest.TestCase):
    def setUp(self):
        self.base=tree();self.sel=selected();self.selected=c.encode(self.sel,b'\0'*16,0)
        self.saved=c.SELECTED_SHA;c.SELECTED_SHA=hashlib.sha256(self.selected).hexdigest()
    def tearDown(self):c.SELECTED_SHA=self.saved
    def run_compose(self):return c.compose(c.encode(self.base,b'\0'*16,0),self.selected)
    def test_actual_reference_collision_preserves_selected_and_boot(self):
        blob,r=self.run_compose();n,_,_=c.decode(blob)
        self.assertEqual(c.words(n['/pcie2-contract-soc/reset-controller@119500']['phandle']),[0x23])
        self.assertEqual(c.words(n['/soc/clock']['phandle']),[0x10023])
        self.assertEqual(c.words(n['/soc/uart@1000']['clocks']),[0x10023,2])
        self.assertEqual(c.words(n['/soc/gic']['phandle']),[1])
        self.assertEqual(c.words(n['/soc/uart@1000']['interrupt-parent']),[1])
        self.assertEqual(n['/chosen']['stdout-path'],self.base['/chosen']['stdout-path'])
        self.assertEqual(n[c.HOST]['ranges'],self.sel[c.HOST]['ranges'])
        self.assertEqual(n[c.HOST+'/rp1']['status'],b'disabled\0')
        self.assertNotIn(c.HOST+'/rp1/device',n)
        self.assertEqual(n['/soc/ethernet']['status'],b'disabled\0')
    def test_symbols_are_paths_not_binding_references(self):
        self.base['/__symbols__']={'clocks':text('/soc/clock')}
        self.base['/aliases']={'gpio':text('/soc/clock')}
        blob,_=self.run_compose();n,_,_=c.decode(blob)
        self.assertEqual(n['/__symbols__']['clocks'],text('/soc/clock'))
        self.assertEqual(n['/aliases']['gpio'],text('/soc/clock'))
    def test_overlay_fixups_refuse(self):
        self.base['/__fixups__']={}
        with self.assertRaises(ValueError):self.run_compose()
    def test_legacy_regulator_gpio_reference_renumbers(self):
        self.base['/soc/gpio']={'phandle':c.packed([11]),'#gpio-cells':c.packed([2])}
        self.base['/regulator']={'compatible':text('regulator-fixed'),'gpio':c.packed([11,5,0])}
        blob,_=self.run_compose();n,_,_=c.decode(blob)
        self.assertEqual(c.words(n['/regulator']['gpio']),[0x1000b,5,0])
        self.assertEqual(c.words(n['/soc/gpio']['phandle']),[0x1000b])
    def test_dangling_reference_refuses(self):
        self.base['/soc/uart@1000']['clocks']=c.packed([0x9999,2])
        with self.assertRaises(ValueError):self.run_compose()
    def test_truncated_reference_refuses(self):
        self.base['/soc/uart@1000']['clocks']=b'\0'
        with self.assertRaises(ValueError):self.run_compose()
    def test_duplicate_phandle_refuses(self):
        self.base['/soc/clock']['phandle']=c.packed([5])
        with self.assertRaises(ValueError):self.run_compose()
    def test_wrong_selected_digest_refuses(self):
        with self.assertRaises(ValueError):c.compose(c.encode(self.base,b'\0'*16,0),self.selected+b'\0')
    def test_existing_synthetic_parent_refuses(self):
        self.base['/axi']={}
        with self.assertRaises(ValueError):self.run_compose()
    def test_reservation_map_survives(self):
        reserve=__import__('struct').pack('>QQQQ',0x1000,0x2000,0,0)
        blob,_=c.compose(c.encode(self.base,reserve,0),self.selected)
        self.assertEqual(c.decode(blob)[1],reserve)

if __name__=='__main__':unittest.main(verbosity=2)
