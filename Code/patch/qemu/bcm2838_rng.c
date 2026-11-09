/* SPDX-License-Identifier: GPL-2.0-or-later */
/* BCM2711 RNG200 polling/control model. Random words come from QEMU's
 * guest-random service. No oscillator timing or health-test fidelity is claimed.
 */
#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/guest-random.h"
#include "qemu/module.h"
#include "hw/irq.h"
#include "hw/misc/bcm2838_rng.h"
#include "migration/vmstate.h"

#define STARTUP_DONE (1u << 17)
#define TOTAL_REACHED 1u

static bool ready(BCM2838RngState *s)
{
    return (s->ctrl & 1) && !(s->rng_reset & 1) && !(s->rbg_reset & 1);
}

static void update_irq(BCM2838RngState *s)
{
    qemu_set_irq(s->irq, (s->status & s->enable) != 0);
}

static uint64_t rng_read(void *opaque, hwaddr offset, unsigned size)
{
    BCM2838RngState *s = opaque;
    uint32_t word;

    switch (offset) {
    case 0x00: return s->ctrl;
    case 0x04: return s->rng_reset;
    case 0x08: return s->rbg_reset;
    case 0x0c: return s->total;
    case 0x10: return s->threshold;
    case 0x18: return s->status;
    case 0x1c: return s->enable;
    case 0x20:
        if (!ready(s)) {
            return 0;
        }
        qemu_guest_getrandom_nofail(&word, sizeof(word));
        s->total += 32;
        if (s->threshold && s->total >= s->threshold) {
            s->status |= TOTAL_REACHED;
            update_irq(s);
        }
        return word;
    case 0x24:
        /* On-demand source: one immediately available word while enabled. */
        return (s->fifo_threshold << 8) | (ready(s) ? 1 : 0);
    default:
        qemu_log_mask(LOG_GUEST_ERROR, "rng200: invalid read at 0x%" HWADDR_PRIx "\n", offset);
        return 0;
    }
}

static void rng_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    BCM2838RngState *s = opaque;
    bool was_ready = ready(s);

    switch (offset) {
    case 0x00: s->ctrl = value; break;
    case 0x04: s->rng_reset = value & 1; break;
    case 0x08: s->rbg_reset = value & 1; break;
    case 0x10: s->threshold = value; break;
    case 0x18: s->status &= ~value; break; /* Write-one-to-clear. */
    case 0x1c: s->enable = value; break;
    case 0x24: s->fifo_threshold = (value >> 8) & 0xff; break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR, "rng200: invalid write at 0x%" HWADDR_PRIx "\n", offset);
        return;
    }
    if (s->rng_reset || s->rbg_reset) {
        s->total = 0;
        s->status = 0;
    } else if (!was_ready && ready(s)) {
        s->status |= STARTUP_DONE;
    }
    update_irq(s);
}

static const MemoryRegionOps rng_ops = {
    .read = rng_read, .write = rng_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static void rng_reset(DeviceState *dev)
{
    BCM2838RngState *s = BCM2838_RNG(dev);
    s->ctrl = s->rng_reset = s->rbg_reset = 0;
    s->total = s->threshold = s->status = s->enable = s->fifo_threshold = 0;
    update_irq(s);
}

static int rng_post_load(void *opaque, int version)
{
    update_irq(opaque);
    return 0;
}

static const VMStateDescription vmstate_rng = {
    .name = TYPE_BCM2838_RNG, .version_id = 1, .minimum_version_id = 1,
    .post_load = rng_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(ctrl, BCM2838RngState),
        VMSTATE_UINT32(rng_reset, BCM2838RngState),
        VMSTATE_UINT32(rbg_reset, BCM2838RngState),
        VMSTATE_UINT32(total, BCM2838RngState),
        VMSTATE_UINT32(threshold, BCM2838RngState),
        VMSTATE_UINT32(status, BCM2838RngState),
        VMSTATE_UINT32(enable, BCM2838RngState),
        VMSTATE_UINT32(fifo_threshold, BCM2838RngState),
        VMSTATE_END_OF_LIST()
    }
};

static void rng_init(Object *obj)
{
    BCM2838RngState *s = BCM2838_RNG(obj);
    memory_region_init_io(&s->iomem, obj, &rng_ops, s, TYPE_BCM2838_RNG, 0x28);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
}

static void rng_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, rng_reset);
    dc->vmsd = &vmstate_rng;
}

static const TypeInfo rng_info = {
    .name = TYPE_BCM2838_RNG, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2838RngState),
    .instance_init = rng_init, .class_init = rng_class_init,
};

static void register_rng(void)
{
    type_register_static(&rng_info);
}
type_init(register_rng)
