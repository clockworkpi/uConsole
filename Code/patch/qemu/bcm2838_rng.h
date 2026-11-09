/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2838_RNG_H
#define HW_MISC_BCM2838_RNG_H

#include "hw/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2838_RNG "bcm2838-rng200"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2838RngState, BCM2838_RNG)

struct BCM2838RngState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    uint32_t ctrl, rng_reset, rbg_reset;
    uint32_t total, threshold, status, enable, fifo_threshold;
};
#endif
