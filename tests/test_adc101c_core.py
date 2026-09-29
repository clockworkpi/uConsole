"""Compile and execute the same digital core intended for the QEMU adapter."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROGRAM = r'''
#include <assert.h>
#include "adc101c-core.h"

static void select_reg(Adc101cCore *s, uint8_t reg) {
    assert(adc101c_begin(s, true));
    assert(adc101c_send(s, reg));
}
static void write8(Adc101cCore *s, uint8_t reg, uint8_t value) {
    select_reg(s, reg);
    assert(adc101c_send(s, value));
}
static void write16(Adc101cCore *s, uint8_t reg, uint16_t value) {
    select_reg(s, reg);
    assert(adc101c_send(s, value >> 8));
    assert(adc101c_send(s, value));
}
static unsigned read16(Adc101cCore *s, uint8_t reg) {
    select_reg(s, reg);
    assert(adc101c_begin(s, false));
    unsigned high = adc101c_recv(s);
    return (high << 8) | adc101c_recv(s);
}
static void next_sample(Adc101cCore *s, unsigned code) {
    /* 4.096 V makes each LSB exactly 4000 uV. */
    assert(adc101c_input(s, code * 4000, 4096000));
    uint64_t finish = s->phase == ADC_INTERVAL ? s->deadline + 1400
                                             : s->started + 1400;
    assert(adc101c_advance(s, finish));
}
int main(void) {
    Adc101cCore s;
    adc101c_init(&s);
    assert(adc101c_valid_state(&s));
    assert(read16(&s, 4) == 0xffc && read16(&s, 6) == 0xffc);
    assert(!adc101c_alert_enabled(&s));
    assert(adc101c_input(&s, 100 * 4000, 4096000));
    assert(read16(&s, 0) == 0); /* Previous sample, then start conversion. */
    assert(s.deadline == 400);
    assert(adc101c_advance(&s, 399));
    assert(adc101c_valid_state(&s));
    assert(s.reg[0] == 0);
    assert(adc101c_advance(&s, 400));
    assert(adc101c_valid_state(&s));
    assert(adc101c_input(&s, 200 * 4000, 4096000));
    assert(adc101c_advance(&s, 1399));
    assert(s.reg[0] == 0);
    assert(adc101c_advance(&s, 1400));
    assert(adc101c_valid_state(&s));
    assert(s.reg[0] == 100 * 4); /* Sample/hold precedes completion. */
    assert(s.reg[6] == 0xffc && s.reg[7] == 0); /* Normal mode, no extrema. */
    assert(!adc101c_advance(&s, 1399));
    assert(s.now == 1400);
    assert(!adc101c_input(&s, 3300001, 3300000));
    assert(!adc101c_input(&s, 1, 0));
    assert(s.reference_uv == 4096000);
    assert(adc101c_input(&s, 1999, 4096000));
    assert(adc101c_quantize(&s) == 0);
    assert(adc101c_input(&s, 2000, 4096000));
    assert(adc101c_quantize(&s) == 4);
    assert(adc101c_input(&s, 4096000, 4096000));
    assert(adc101c_quantize(&s) == 0xffc);

    adc101c_reset(&s);
    write16(&s, 3, 100 * 4);
    write16(&s, 4, 900 * 4);
    write16(&s, 5, 10 * 4);
    write8(&s, 2, 0x2c); /* Auto, flag and active-low pin enabled. */
    assert(adc101c_period(&s) == 32000);
    next_sample(&s, 99);
    assert(s.reg[1] == 1 && s.reg[6] == 99 * 4 && s.reg[7] == 99 * 4);
    assert(adc101c_value(&s, 0) == (0x8000 | (99 * 4)));
    assert(adc101c_alert_enabled(&s) && !adc101c_alert_level(&s));
    next_sample(&s, 110); /* Exact hysteresis boundary does not clear. */
    assert(s.reg[1] == 1);
    next_sample(&s, 111);
    assert(s.reg[1] == 0 && adc101c_alert_level(&s));
    next_sample(&s, 901);
    assert(s.reg[1] == 2 && s.reg[7] == 901 * 4);
    next_sample(&s, 890);
    assert(s.reg[1] == 2);
    next_sample(&s, 889);
    assert(s.reg[1] == 0);
    write8(&s, 2, 0x3d); /* Hold, active high. */
    next_sample(&s, 901);
    next_sample(&s, 500);
    assert(s.reg[1] == 2 && adc101c_alert_level(&s));
    write8(&s, 1, 1);
    assert(s.reg[1] == 2); /* Selective W1C. */
    write8(&s, 1, 2);
    assert(s.reg[1] == 0 && !adc101c_alert_level(&s));
    next_sample(&s, 99);
    write8(&s, 1, 1);
    assert(s.reg[1] == 0);
    next_sample(&s, 99);
    assert(s.reg[1] == 1); /* Reassert only after another conversion. */
    write16(&s, 6, 0xffff);
    write16(&s, 7, 0);
    assert(s.reg[6] == 0xffc && s.reg[7] == 0);
    write8(&s, 2, 2); /* Reserved bit discarded; stop automatic conversion. */
    assert(s.reg[2] == 0 && s.deadline == ADC_NEVER);
    assert(!adc101c_alert_enabled(&s));

    /* No partial write commits; pointer persists, word reads stay coherent. */
    select_reg(&s, 3);
    assert(adc101c_send(&s, 0x09));
    assert(adc101c_begin(&s, false));
    unsigned high = adc101c_recv(&s);
    assert((high << 8 | adc101c_recv(&s)) == 400);
    select_reg(&s, 0);
    assert(!adc101c_send(&s, 0xff));
    assert(adc101c_begin(&s, true));
    assert(!adc101c_send(&s, 8));
    adc101c_power(&s, false);
    assert(!adc101c_begin(&s, false) && !adc101c_send(&s, 0));
    assert(adc101c_recv(&s) == 0xff && s.deadline == ADC_NEVER);
    adc101c_power(&s, true);
    assert(s.reg[0] == 0 && s.reg[1] == 0 && s.reg[4] == 0xffc);
    assert(s.input_uv == 99 * 4000 && s.reference_uv == 4096000);
    for (unsigned cycle = 1; cycle <= 7; cycle++) {
        write8(&s, 2, cycle << 5);
        assert(adc101c_period(&s) == (UINT64_C(1000) << (cycle + 4)));
        assert(adc101c_valid_state(&s));
    }
    assert(adc101c_advance(&s, s.started + 1400));
    assert(s.phase == ADC_INTERVAL && adc101c_valid_state(&s));
    Adc101cCore saved = s;
    s.pointer = 8; assert(!adc101c_valid_state(&s)); s = saved;
    s.position = 2; assert(!adc101c_valid_state(&s)); s = saved;
    s.phase = 4; assert(!adc101c_valid_state(&s)); s = saved;
    s.deadline = s.now; assert(!adc101c_valid_state(&s)); s = saved;
    s.deadline++; assert(!adc101c_valid_state(&s)); s = saved;
    s.reference_uv = 0; assert(!adc101c_valid_state(&s)); s = saved;
    s.input_uv = s.reference_uv + 1; assert(!adc101c_valid_state(&s)); s = saved;
    s.reg[2] |= 2; assert(!adc101c_valid_state(&s)); s = saved;
    s.reg[0] |= 1; assert(!adc101c_valid_state(&s)); s = saved;
    s.powered = false; assert(!adc101c_valid_state(&s)); s = saved;
    adc101c_power(&s, false); assert(adc101c_valid_state(&s));
    s.deadline = s.now + 1; assert(!adc101c_valid_state(&s));
    return 0;
}
'''


class ADC101CCoreTests(unittest.TestCase):
    def test_native_register_conversion_and_fault_contract(self):
        compiler = shutil.which('cc')
        if compiler is None:
            self.skipTest('C compiler unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'adc.c'
            source.write_text(PROGRAM)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=undefined', '-I', str(ROOT / 'Code/patch/qemu'),
                            str(source), '-o', str(root / 'adc')], check=True,
                           capture_output=True, text=True, timeout=30)
            subprocess.run([str(root / 'adc')], check=True, capture_output=True,
                           text=True, timeout=10)
