/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * ADC101C digital core. Time is supplied by the caller in nanoseconds.
 * Reference: TI SNAS446D. No electrical, oscillator-tolerance or bus-bit timing
 * claim. Nominal acquisition/conversion durations are 400/1000 ns.
 * A QEMU adapter must drive advance() from QEMU_CLOCK_VIRTUAL, not wall time.
 */
#ifndef UCONSOLE_ADC101C_CORE_H
#define UCONSOLE_ADC101C_CORE_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

typedef struct Adc101cCore {
    uint16_t reg[8];
    uint16_t sampled, read_latch, write_latch;
    uint32_t input_uv, reference_uv;
    uint64_t now, deadline, started;
    uint8_t pointer, position, phase;
    bool expect_pointer, powered, automatic_sample;
} Adc101cCore;

enum { ADC_IDLE, ADC_ACQUIRE, ADC_CONVERT, ADC_INTERVAL };
#define ADC_NEVER UINT64_MAX

static inline unsigned adc101c_cycle(const Adc101cCore *s)
{
    return s->reg[2] >> 5;
}

static inline uint64_t adc101c_period(const Adc101cCore *s)
{
    unsigned cycle = adc101c_cycle(s);
    return cycle ? (UINT64_C(1000) << (cycle + 4)) : 0;
}

/* Validate an external saved state before scheduling timers or using indices. */
static inline bool adc101c_valid_state(const Adc101cCore *s)
{
    if (s->reference_uv < 2700000 || s->reference_uv > 5500000 ||
        s->input_uv > s->reference_uv || s->pointer > 7 || s->position > 1 ||
        s->phase > ADC_INTERVAL || s->now > INT64_MAX - UINT64_C(4096000) ||
        s->started > s->now || (s->sampled & ~0xffc) ||
        (s->write_latch & 0xff) || (s->reg[1] & ~3) || (s->reg[2] & ~0xfd)) {
        return false;
    }
    for (unsigned i = 0; i < 8; i++) {
        if (i != 1 && i != 2 && (s->reg[i] & ~0xffc)) {
            return false;
        }
    }
    if (s->phase == ADC_IDLE) {
        return s->deadline == ADC_NEVER;
    }
    if (!s->powered || s->deadline <= s->now ||
        s->automatic_sample != (adc101c_cycle(s) != 0)) {
        return false;
    }
    uint64_t duration = s->phase == ADC_ACQUIRE ? 400 :
                        s->phase == ADC_CONVERT ? 1400 : adc101c_period(s);
    return duration && s->deadline == s->started + duration;
}

static inline uint16_t adc101c_quantize(const Adc101cCore *s)
{
    uint64_t code = ((uint64_t)s->input_uv * 1024 + s->reference_uv / 2)
                    / s->reference_uv;
    return (code > 1023 ? 1023 : code) << 2;
}

static inline void adc101c_start(Adc101cCore *s)
{
    s->started = s->now;
    s->phase = ADC_ACQUIRE;
    s->deadline = s->now + 400;
    s->automatic_sample = adc101c_cycle(s) != 0;
}

static inline void adc101c_complete(Adc101cCore *s)
{
    int value = s->sampled >> 2;
    int low = s->reg[3] >> 2, high = s->reg[4] >> 2;
    int hysteresis = s->reg[5] >> 2;
    s->reg[0] = s->sampled;
    if (value < low) {
        s->reg[1] |= 1;
    } else if (!(s->reg[2] & 0x10) && value > low + hysteresis) {
        s->reg[1] &= ~1;
    }
    if (value > high) {
        s->reg[1] |= 2;
    } else if (!(s->reg[2] & 0x10) && value < high - hysteresis) {
        s->reg[1] &= ~2;
    }
    if (s->automatic_sample) {
        if (s->sampled < s->reg[6]) {
            s->reg[6] = s->sampled;
        }
        if (s->sampled > s->reg[7]) {
            s->reg[7] = s->sampled;
        }
    }
}

/* Returns false for backwards time or deadlines outside the supported range. */
static inline bool adc101c_advance(Adc101cCore *s, uint64_t now)
{
    if (now < s->now || now > UINT64_MAX - UINT64_C(4096000)) {
        return false;
    }
    while (s->deadline != ADC_NEVER && s->deadline <= now) {
        s->now = s->deadline;
        if (s->phase == ADC_ACQUIRE) {
            s->sampled = adc101c_quantize(s);
            s->phase = ADC_CONVERT;
            s->deadline = s->now + 1000;
        } else if (s->phase == ADC_CONVERT) {
            adc101c_complete(s);
            if (adc101c_cycle(s)) {
                s->phase = ADC_INTERVAL;
                s->deadline = s->started + adc101c_period(s);
            } else {
                s->phase = ADC_IDLE;
                s->deadline = ADC_NEVER;
            }
        } else if (s->phase == ADC_INTERVAL) {
            adc101c_start(s);
        } else {
            s->deadline = ADC_NEVER;
        }
    }
    s->now = now;
    return true;
}

/* Reset preserves the externally supplied voltage and power inputs. */
static inline void adc101c_reset(Adc101cCore *s)
{
    memset(s->reg, 0, sizeof(s->reg));
    s->reg[4] = s->reg[6] = 0xffc;
    s->pointer = s->position = s->phase = 0;
    s->sampled = s->read_latch = s->write_latch = 0;
    s->expect_pointer = true;
    s->automatic_sample = false;
    s->deadline = ADC_NEVER;
}

static inline void adc101c_init(Adc101cCore *s)
{
    memset(s, 0, sizeof(*s));
    s->reference_uv = 3300000;
    s->powered = true;
    adc101c_reset(s);
}

static inline void adc101c_power(Adc101cCore *s, bool powered)
{
    if (s->powered != powered) {
        s->powered = powered;
        adc101c_reset(s);
    }
}

static inline bool adc101c_input(Adc101cCore *s, uint32_t input, uint32_t reference)
{
    if (reference < 2700000 || reference > 5500000 || input > reference) {
        return false;
    }
    s->input_uv = input;
    s->reference_uv = reference;
    return true;
}

static inline unsigned adc101c_width(const Adc101cCore *s)
{
    return s->pointer == 1 || s->pointer == 2 ? 1 : 2;
}

static inline uint16_t adc101c_value(const Adc101cCore *s, unsigned reg)
{
    uint16_t value = s->reg[reg];
    if (reg == 0 && (s->reg[2] & 8) && s->reg[1]) {
        value |= 0x8000;
    }
    return value;
}

/* Two outputs represent pin drive enable and logical level, including Hi-Z. */
static inline bool adc101c_alert_enabled(const Adc101cCore *s)
{
    return s->powered && (s->reg[2] & 4);
}

static inline bool adc101c_alert_level(const Adc101cCore *s)
{
    return (s->reg[1] != 0) == ((s->reg[2] & 1) != 0);
}

static inline bool adc101c_begin(Adc101cCore *s, bool write)
{
    s->position = 0;
    s->expect_pointer = write;
    return s->powered;
}

static inline bool adc101c_send(Adc101cCore *s, uint8_t byte)
{
    if (!s->powered) {
        return false;
    }
    if (s->expect_pointer) {
        if (byte > 7) {
            return false;
        }
        s->pointer = byte;
        s->expect_pointer = false;
        return true;
    }
    if (s->pointer == 0) {
        return false; /* Conversion register is read-only. */
    }
    if (adc101c_width(s) == 2 && s->position++ == 0) {
        s->write_latch = (uint16_t)byte << 8;
        return true;
    }
    if (s->pointer == 1) {
        s->reg[1] &= ~(byte & 3);
    } else if (s->pointer == 2) {
        unsigned old_cycle = adc101c_cycle(s);
        s->reg[2] = byte & 0xfd;
        if (old_cycle != adc101c_cycle(s)) {
            s->phase = ADC_IDLE;
            s->deadline = ADC_NEVER;
            if (adc101c_cycle(s)) {
                adc101c_start(s);
            }
        }
    } else {
        s->reg[s->pointer] = (s->write_latch | byte) & 0xffc;
    }
    s->position = 0;
    return true;
}

static inline uint8_t adc101c_recv(Adc101cCore *s)
{
    uint8_t result;
    if (!s->powered) {
        return 0xff;
    }
    if (s->position == 0) {
        s->read_latch = adc101c_value(s, s->pointer);
    }
    if (adc101c_width(s) == 1) {
        return s->read_latch;
    }
    result = s->position == 0 ? s->read_latch >> 8 : s->read_latch;
    s->position ^= 1;
    if (s->pointer == 0 && s->position == 0 && !adc101c_cycle(s)
            && s->phase == ADC_IDLE) {
        adc101c_start(s);
    }
    return result;
}

#endif
