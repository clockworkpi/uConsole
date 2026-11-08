/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2838_GENET_H
#define HW_MISC_BCM2838_GENET_H
#include "hw/sysbus.h"
#define TYPE_BCM2838_GENET "bcm2838-genet-disconnected"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2838GenetState, BCM2838_GENET)
#define GENET_CONTROL_WORDS 12
#define GENET_RING_WORDS (17 * 16)
#define GENET_DESCRIPTOR_WORDS (256 * 3)
struct BCM2838GenetState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq[2];
    uint32_t control[GENET_CONTROL_WORDS];
    uint32_t mac_command, mac[2], max_frame, mib_control, tx_flush;
    uint32_t mdio_command, filter_control, filters[34];
    uint32_t irq_status[2], irq_mask[2];
    uint32_t descriptors[2][GENET_DESCRIPTOR_WORDS];
    uint32_t rings[2][GENET_RING_WORDS];
    uint32_t dma_config[2], dma_control[2], dma_burst[2];
    uint16_t phy[32], phy_aux[8], phy_shadow[32];
    uint8_t aux_select, shadow_select;
};
#endif
