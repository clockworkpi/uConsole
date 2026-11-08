/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2838_PCIE_H
#define HW_MISC_BCM2838_PCIE_H

#include "hw/sysbus.h"

#define TYPE_BCM2838_PCIE "bcm2838-pcie-disconnected"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2838PcieState, BCM2838_PCIE)
#define BCM2838_PCIE_NREGS 36

/* Controller-only profile. There is deliberately no PCI bus or endpoint. */
struct BCM2838PcieState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq[2];
    uint32_t regs[BCM2838_PCIE_NREGS];
    uint32_t reset;
    uint32_t config_index;
    uint32_t status[2];
    uint32_t mask[2];
};
#endif
