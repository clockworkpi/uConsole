/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2711_AVS_THERMAL_H
#define HW_MISC_BCM2711_AVS_THERMAL_H

#include "hw/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2711_AVS_THERMAL "bcm2711-avs-thermal"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2711AvsThermalState, BCM2711_AVS_THERMAL)

struct BCM2711AvsThermalState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    int32_t temperature_millicelsius;
};

#endif
