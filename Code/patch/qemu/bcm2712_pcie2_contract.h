/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2712_PCIE2_CONTRACT_H
#define HW_MISC_BCM2712_PCIE2_CONTRACT_H

#include "hw/pci/pci.h"
#include "hw/pci/pci_host.h"

/* Explicit synthetic contract target; never advertised as a Pi500 board. */
#define TYPE_BCM2712_PCIE2_CONTRACT "bcm2712-pcie2-contract"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2712Pcie2ContractState, BCM2712_PCIE2_CONTRACT)
#define BCM2712_PCIE2_CONTRACT_NREGS 81

typedef struct BCM2712ContractProvider {
    BCM2712Pcie2ContractState *s;
    unsigned kind;
} BCM2712ContractProvider;

struct BCM2712Pcie2ContractState {
    PCIHostState parent_obj;
    MemoryRegion mmio, cpu_mmio, pci_mmio, pci_io, dma_memory;
    MemoryRegion outbound[4], inbound[3], provider_mr[5];
    BCM2712ContractProvider provider[5];
    AddressSpace dma_as;
    MemoryRegion *ram;
    PCIDevice *root_port, *xhci;
    qemu_irq irq[6]; /* controller, MSI bank, then native INTx A..D */
    bool connected_xhci, migration_connected, regions_ready;
    bool intx_level[4];
    uint32_t regs[BCM2712_PCIE2_CONTRACT_NREGS];
    uint32_t reset, config_index;
    uint32_t status[2], mask[2];
    uint32_t mdio_addr, mdio_write, mdio_read;
    uint16_t mdio_bank, mdio_ssc_control;
    uint16_t mdio_table[7];
    uint8_t mdio_programmed;
    uint32_t mip_config[2], mip_host_mask[2], mip_vpu_mask[2];
    uint32_t sw_init[2], rescal_start, rescal_status;
    uint32_t gpio_data[2], gpio_dir[2], gpio_mask[2];
    uint32_t pin_mux, pin_bias;
    bool calibrated;
    qemu_irq mip_irq[64];
};
#endif
