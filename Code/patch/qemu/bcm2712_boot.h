/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2712_BOOT_H
#define HW_MISC_BCM2712_BOOT_H
#include "hw/sysbus.h"
#include "hw/sd/sdhci.h"
#define TYPE_BCM2712_BOOT "bcm2712-boot-registers"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2712BootState, BCM2712_BOOT)
struct BCM2712BootState {
    SysBusDevice parent_obj;
    MemoryRegion cfg_mr, pin_mr, aon_mr, gpio_mr;
    DeviceState *sdhci;
    uint32_t cfg_ctrl, cfg_mode;
    uint32_t pin[8], aon[7], gpio[16];
};
#endif
