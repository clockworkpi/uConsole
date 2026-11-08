/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_BCM2838_PCIE_H
#define HW_MISC_BCM2838_PCIE_H

#include "hw/pci/pci.h"
#include "hw/pci/pci_host.h"

/* Keep the original QOM name for existing disconnected-profile invocations. */
#define TYPE_BCM2838_PCIE "bcm2838-pcie-disconnected"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2838PcieState, BCM2838_PCIE)
#define BCM2838_PCIE_NREGS 36

struct BCM2838PcieState {
    PCIHostState parent_obj;
    MemoryRegion mmio, cpu_mmio, pci_mmio, pci_io, dma_memory;
    MemoryRegion outbound[4], inbound;
    AddressSpace dma_as;
    MemoryRegion *ram;
    PCIDevice *root_port, *xhci;
    qemu_irq irq[6]; /* controller, MSI bank, then native INTx A..D */
    bool connected_xhci, migration_connected, regions_ready;
    bool intx_level[4];
    uint32_t regs[BCM2838_PCIE_NREGS];
    uint32_t reset, config_index;
    uint32_t status[2], mask[2];
    uint32_t mdio_addr, mdio_write, mdio_read;
    uint16_t mdio_bank, mdio_ssc_control;
};
#endif
