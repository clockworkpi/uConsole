/* SPDX-License-Identifier: GPL-2.0-or-later */
/* BCM2712 D0 boot registers, selected SD/pad/polled GPIO subset.
 * Register layout: raspberrypi/linux sdhci-brcmstb.c, gpio-brcmstb.c,
 * pinctrl-bcm2712.c and enrolled Pi500 D0 firmware FDT.
 * This is digital register behavior; pad voltages/analog timing/IRQ GPIO
 * delivery are not modeled. Virtual reset values are not hardware measurements.
 */
#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/qdev-properties.h"
#include "hw/misc/bcm2712_boot.h"
#include "migration/vmstate.h"

static uint64_t cfg_read(void *opaque, hwaddr off, unsigned size)
{
    BCM2712BootState *s = opaque;
    switch (off) {
    case 0: return s->cfg_ctrl;
    case 0x1ac: return s->cfg_mode;
    default: return 0;
    }
}
static void cfg_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    BCM2712BootState *s = opaque;
    switch (off) {
    case 0: s->cfg_ctrl = val & 0xc0000000U; break;
    case 0x1ac: s->cfg_mode = val & 0x80000001U; break;
    default:
        qemu_log_mask(LOG_UNIMP, "bcm2712 SD CFG write 0x%" HWADDR_PRIx "\n", off);
    }
}
static uint64_t pin_read(void *opaque, hwaddr off, unsigned size)
{
    BCM2712BootState *s = opaque;
    return s->pin[off / 4];
}
static void pin_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    BCM2712BootState *s = opaque;
    s->pin[off / 4] = val;
}
static uint64_t aon_read(void *opaque, hwaddr off, unsigned size)
{
    BCM2712BootState *s = opaque;
    return s->aon[off / 4];
}
static void aon_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    BCM2712BootState *s = opaque;
    s->aon[off / 4] = val;
}
static uint64_t gpio_read(void *opaque, hwaddr off, unsigned size)
{
    BCM2712BootState *s = opaque;
    unsigned bank = off / 32;
    uint32_t val = s->gpio[off / 4];
    if (off % 32 == 4 && bank == 0) {
        /* Board SD_CD_N is input pin5, active low. Reflect the actual
         * standard QEMU SD card/backend, not a constant card-present bit. */
        bool present = s->sdhci && sdbus_get_inserted(&SYSBUS_SDHCI(s->sdhci)->sdbus);
        val = (val & ~32U) | (present ? 0 : 32);
    }
    return val;
}
static void gpio_write(void *opaque, hwaddr off, uint64_t val, unsigned size)
{
    BCM2712BootState *s = opaque;
    uint32_t mask = off < 32 ? 0x7fff : 0x3f;
    unsigned index = off / 4;
    if (off % 32 == 0x1c) {
        s->gpio[index] &= ~val; /* interrupt status W1C; no interrupt inputs */
    } else {
        s->gpio[index] = val & mask;
    }
}
#define OPS(name) static const MemoryRegionOps name##_ops = { \
    .read = name##_read, .write = name##_write, \
    .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid.min_access_size = 4, .valid.max_access_size = 4, \
    .impl.min_access_size = 4, .impl.max_access_size = 4, \
}
OPS(cfg);
OPS(pin);
OPS(aon);
OPS(gpio);
static void boot_reset(DeviceState *dev)
{
    BCM2712BootState *s = BCM2712_BOOT(dev);
    s->cfg_ctrl = s->cfg_mode = 0;
    memset(s->pin, 0, sizeof(s->pin));
    memset(s->aon, 0, sizeof(s->aon));
    memset(s->gpio, 0, sizeof(s->gpio));
    s->gpio[2] = 0x7fff;
    s->gpio[10] = 0x3f;
}
static const VMStateDescription vmstate_boot = {
    .name = TYPE_BCM2712_BOOT, .version_id = 1, .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(cfg_ctrl, BCM2712BootState),
        VMSTATE_UINT32(cfg_mode, BCM2712BootState),
        VMSTATE_UINT32_ARRAY(pin, BCM2712BootState, 8),
        VMSTATE_UINT32_ARRAY(aon, BCM2712BootState, 7),
        VMSTATE_UINT32_ARRAY(gpio, BCM2712BootState, 16),
        VMSTATE_END_OF_LIST()
    }
};
static const Property boot_properties[] = {
    DEFINE_PROP_LINK("sdhci", BCM2712BootState, sdhci,
                     TYPE_SYSBUS_SDHCI, DeviceState *),
};
static void boot_init(Object *obj)
{
    BCM2712BootState *s = BCM2712_BOOT(obj);
    memory_region_init_io(&s->cfg_mr, obj, &cfg_ops, s, "pi5.sd-cfg", 0x200);
    memory_region_init_io(&s->pin_mr, obj, &pin_ops, s, "pi5.pinctrl", 0x20);
    memory_region_init_io(&s->aon_mr, obj, &aon_ops, s, "pi5.aon-pinctrl", 0x1c);
    memory_region_init_io(&s->gpio_mr, obj, &gpio_ops, s, "pi5.aon-gpio", 0x40);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->cfg_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->pin_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->aon_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->gpio_mr);
}
static void boot_class_init(ObjectClass *oc, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    dc->vmsd = &vmstate_boot;
    device_class_set_props(dc, boot_properties);
    device_class_set_legacy_reset(dc, boot_reset);
}
static const TypeInfo boot_type = {
    .name = TYPE_BCM2712_BOOT, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2712BootState), .instance_init = boot_init,
    .class_init = boot_class_init,
};
static void boot_register_types(void) { type_register_static(&boot_type); }
type_init(boot_register_types)
