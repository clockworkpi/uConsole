/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * BCM2711 PCIe control and optional connected QEMU root-port/xHCI profile.
 *
 * The default remains a disconnected port. Opt-in connected-xhci uses actual
 * PCI devices/configuration, programmed CPU/PCI windows, a BAR2 RAM DMA window
 * and native INTx. PCI IDs describe QEMU devices, not a fabricated VL805.
 * PHY/MDIO timing and endpoint-generated MSI are not implemented. Unsupported
 * controller accesses fail rather than becoming blanket register RAM.
 * Register definitions: Linux v6.12 pcie-brcmstb.c and EDK2 Bcm2711.h.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "hw/pci/pci_bridge.h"
#include "hw/pci/pci_bus.h"
#include "hw/misc/bcm2838_pcie.h"
#include "migration/vmstate.h"

#define SW_INIT 0x9210
#define BRIDGE_RESET 2
#define PERST 1
#define PORT_STATUS 0x4068
#define REVISION 0x406c

static const hwaddr offsets[BCM2838_PCIE_NREGS] = {
    0x0188, 0x043c, 0x04dc, 0x04f8,
    0x4008, 0x400c, 0x4010, 0x4014, 0x4018, 0x401c, 0x4020,
    0x4024, 0x4028, 0x402c, 0x4030, 0x4034, 0x4038, 0x403c,
    0x4040, 0x4044, 0x4048, 0x404c, 0x4064,
    0x4070, 0x4074, 0x4078, 0x407c,
    0x4080, 0x4084, 0x4088, 0x408c, 0x4090, 0x4094, 0x4098, 0x409c,
    0x4204,
};

static int slot(hwaddr offset)
{
    for (int n = 0; n < ARRAY_SIZE(offsets); n++) {
        if (offsets[n] == offset) {
            return n;
        }
    }
    return -1;
}

static uint32_t reg(BCM2838PcieState *s, hwaddr offset)
{
    int n = slot(offset);
    assert(n >= 0);
    return s->regs[n];
}

static bool link_up(BCM2838PcieState *s)
{
    return s->connected_xhci && !s->reset &&
           !(reg(s, 0x4204) & (1u << 27));
}

static void update_irq(BCM2838PcieState *s)
{
    for (int n = 0; n < 2; n++) {
        qemu_set_irq(s->irq[n], !(s->reset & BRIDGE_RESET) &&
                     (s->status[n] & ~s->mask[n]));
    }
    for (int n = 0; n < 4; n++) {
        qemu_set_irq(s->irq[n + 2], link_up(s) && s->intx_level[n]);
    }
}

static uint64_t inbound_size(uint32_t code)
{
    if (code >= 1 && code <= 0x14) {
        return 1ULL << (code + 15);
    }
    if (code >= 0x1c && code <= 0x1f) {
        return 1ULL << (code - 0x1c + 12);
    }
    return 0; /* disabled or reserved */
}

static void update_regions(BCM2838PcieState *s)
{
    bool enabled = link_up(s) && (reg(s, 0x4008) & 0x1000);
    uint64_t pci_base, cpu_base, cpu_limit, size;

    if (!s->regions_ready) {
        return;
    }
    memory_region_transaction_begin();
    for (int n = 0; n < 4; n++) {
        pci_base = ((uint64_t)reg(s, 0x4010 + n * 8) << 32) |
                   (reg(s, 0x400c + n * 8) & ~0xfffU);
        cpu_base = ((uint64_t)(reg(s, 0x4080 + n * 8) & 0xff) << 32) |
                   ((uint64_t)(reg(s, 0x4070 + n * 4) & 0xfff0) << 16);
        cpu_limit = ((uint64_t)(reg(s, 0x4084 + n * 8) & 0xff) << 32) |
                    (reg(s, 0x4070 + n * 4) & 0xfff00000) | 0xfffff;
        size = cpu_limit >= cpu_base ? cpu_limit - cpu_base + 1 : 0;
        memory_region_set_enabled(&s->outbound[n], false);
        if (size && pci_base <= UINT64_MAX - size) {
            memory_region_set_alias_offset(&s->outbound[n], pci_base);
            memory_region_set_size(&s->outbound[n], size);
            memory_region_set_address(&s->outbound[n], cpu_base);
            memory_region_set_enabled(&s->outbound[n], enabled);
        }
    }
    /* BCM2711's SCB memory window targets ARM RAM from address zero. BAR2
     * supplies the PCI address and size, including nonidentity DMA ranges. */
    pci_base = ((uint64_t)reg(s, 0x4038) << 32) | (reg(s, 0x4034) & ~31U);
    size = inbound_size(reg(s, 0x4034) & 31);
    memory_region_set_enabled(&s->inbound, false);
    if (size && pci_base <= UINT64_MAX - size) {
        memory_region_set_size(&s->inbound, size);
        memory_region_set_address(&s->inbound, pci_base);
        memory_region_set_enabled(&s->inbound, enabled);
    }
    memory_region_transaction_commit();
}

static void clear_controller(BCM2838PcieState *s)
{
    memset(s->regs, 0, sizeof(s->regs));
    if (s->connected_xhci) {
        /* Disabled reset windows have base above limit. Do not map untouched
         * windows over low ARM RAM when software enables SCB access. */
        for (int n = 0; n < 4; n++) {
            s->regs[slot(0x4070 + n * 4)] = 0x0000fff0;
        }
    }
    memset(s->status, 0, sizeof(s->status));
    memset(s->intx_level, 0, sizeof(s->intx_level));
    s->config_index = 0;
    s->mask[0] = s->mask[1] = UINT32_MAX;
    update_regions(s);
    update_irq(s);
}

static int interrupt_bank(hwaddr offset, hwaddr *regoff)
{
    if (offset >= 0x4300 && offset <= 0x4314) {
        *regoff = offset - 0x4300;
        return 0;
    }
    if (offset >= 0x4500 && offset <= 0x4514) {
        *regoff = offset - 0x4500;
        return 1;
    }
    return -1;
}

static bool config_address(hwaddr off)
{
    return off < 0x1000 || (off >= 0x8000 && off < 0x9000);
}

static PCIDevice *config_device(BCM2838PcieState *s, hwaddr off)
{
    if (!link_up(s)) {
        return NULL;
    }
    if (off < 0x1000) {
        return s->root_port;
    }
    return pci_find_device(s->parent_obj.bus, (s->config_index >> 20) & 0xff,
                           (s->config_index >> 12) & 0xff);
}

static uint32_t config_read(BCM2838PcieState *s, hwaddr off, unsigned size)
{
    PCIDevice *d = config_device(s, off);
    return d ? pci_host_config_read_common(d, off & 0xfff, 4096, size) :
               UINT32_MAX;
}

static void config_write(BCM2838PcieState *s, hwaddr off, uint32_t value,
                         unsigned size)
{
    PCIDevice *d = config_device(s, off);
    if (d) {
        pci_host_config_write_common(d, off & 0xfff, 4096, value, size);
    }
}

static MemTxResult pcie_read(void *opaque, hwaddr off, uint64_t *data,
                             unsigned size, MemTxAttrs attrs)
{
    BCM2838PcieState *s = opaque;
    hwaddr regoff;
    int n = slot(off), bank = interrupt_bank(off, &regoff);

    if (size != 4 || (off & 3)) {
        if (config_address(off)) {
            *data = config_read(s, off, size);
            return MEMTX_OK;
        }
        return MEMTX_ERROR;
    }
    if (off == SW_INIT) {
        *data = s->reset;
    } else if (off == PORT_STATUS) {
        *data = 0x80 | (link_up(s) ? 0x30 : 0);
    } else if (off == REVISION) {
        *data = 0x0320;
    } else if (bank >= 0) {
        *data = regoff < 0x0c ? s->status[bank] : s->mask[bank];
    } else if (n >= 0) {
        *data = s->regs[n];
    } else if (off == 0x9000) {
        *data = s->config_index;
    } else if (config_address(off)) {
        *data = config_read(s, off, size);
    } else {
        qemu_log_mask(LOG_UNIMP, "bcm2711-pcie: unsupported read 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult pcie_write(void *opaque, hwaddr off, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    BCM2838PcieState *s = opaque;
    hwaddr regoff;
    int n = slot(off), bank = interrupt_bank(off, &regoff);

    if (size != 4 || (off & 3)) {
        if (config_address(off)) {
            config_write(s, off, value, size);
            return MEMTX_OK;
        }
        return MEMTX_ERROR;
    }
    if (off == SW_INIT) {
        uint32_t old = s->reset;
        s->reset = value & (BRIDGE_RESET | PERST);
        if ((s->reset & BRIDGE_RESET) && !(old & BRIDGE_RESET)) {
            clear_controller(s);
        }
        if (s->connected_xhci && (s->reset & ~old)) {
            /* A real downstream reset also resets endpoint engine/config. */
            device_cold_reset(DEVICE(s->root_port));
        }
    } else if (off == PORT_STATUS || off == REVISION) {
        /* Read-only straps and identification. */
    } else if (bank >= 0) {
        switch (regoff) {
        case 0x00: case 0x0c: break;
        case 0x04: s->status[bank] |= value; break;
        case 0x08: s->status[bank] &= ~value; break;
        case 0x10: s->mask[bank] |= value; break;
        case 0x14: s->mask[bank] &= ~value; break;
        }
    } else if (n >= 0) {
        if ((off == 0x402c || off == 0x403c) && (value & 31)) {
            qemu_log_mask(LOG_UNIMP, "bcm2711-pcie: only inbound BAR2 is implemented\n");
            return MEMTX_ERROR;
        }
        if (!(s->reset & BRIDGE_RESET)) {
            s->regs[n] = value;
        }
    } else if (off == 0x9000) {
        s->config_index = value & 0x0ffff000;
    } else if (config_address(off)) {
        config_write(s, off, value, size);
    } else {
        qemu_log_mask(LOG_UNIMP, "bcm2711-pcie: unsupported write 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    update_regions(s);
    update_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps pcie_ops = {
    .read_with_attrs = pcie_read, .write_with_attrs = pcie_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};

static void pcie_set_irq(void *opaque, int pin, int level)
{
    BCM2838PcieState *s = opaque;
    s->intx_level[pin] = level;
    update_irq(s);
}

static AddressSpace *pcie_dma_space(PCIBus *bus, void *opaque, int devfn)
{
    BCM2838PcieState *s = opaque;
    return &s->dma_as;
}

static const PCIIOMMUOps pcie_iommu_ops = {
    .get_address_space = pcie_dma_space,
};

static const char *pcie_root_bus_path(PCIHostState *host, PCIBus *bus)
{
    return "0000:00";
}

static void pcie_realize(DeviceState *dev, Error **errp)
{
    BCM2838PcieState *s = BCM2838_PCIE(dev);
    PCIBus *secondary;
    PCIDevice *port, *xhci;

    if (!s->ram) {
        error_setg(errp, "BCM2711 PCIe requires the board RAM link");
        return;
    }
    memory_region_init_alias(&s->inbound, OBJECT(s), "bcm2711-pcie-inbound-bar2",
                              s->ram, 0, 1);
    memory_region_set_enabled(&s->inbound, false);
    memory_region_add_subregion(&s->dma_memory, 0, &s->inbound);
    address_space_init(&s->dma_as, &s->dma_memory, "bcm2711-pcie-dma");
    s->regions_ready = true;
    if (!s->connected_xhci) {
        return;
    }
    s->parent_obj.bus = pci_register_root_bus(dev, "bcm-pcie",
        pcie_set_irq, pci_swizzle_map_irq_fn, s, &s->pci_mmio, &s->pci_io,
        0, 4, TYPE_PCIE_BUS);
    s->parent_obj.bus->flags |= PCI_BUS_EXTENDED_CONFIG_SPACE;
    pci_setup_iommu(s->parent_obj.bus, &pcie_iommu_ops, s);

    port = pci_new(PCI_DEVFN(0, 0), "pcie-root-port");
    object_property_add_child(OBJECT(s), "root-port", OBJECT(port));
    qdev_prop_set_uint8(DEVICE(port), "chassis", 1);
    qdev_prop_set_uint16(DEVICE(port), "slot", 0);
    qdev_prop_set_bit(DEVICE(port), "hotplug", false);
    if (!pci_realize_and_unref(port, s->parent_obj.bus, errp)) {
        return;
    }
    s->root_port = port;
    secondary = pci_bridge_get_sec_bus(PCI_BRIDGE(port));
    xhci = pci_new(PCI_DEVFN(0, 0), "qemu-xhci");
    object_property_add_child(OBJECT(s), "xhci", OBJECT(xhci));
    DEVICE(xhci)->id = g_strdup("bcm-xhci");
    if (!object_property_set_str(OBJECT(xhci), "msi", "off", errp) ||
        !object_property_set_str(OBJECT(xhci), "msix", "off", errp) ||
        !object_property_set_bool(OBJECT(xhci), "compact-mmio", true, errp)) {
        object_unref(xhci);
        return;
    }
    if (!pci_realize_and_unref(xhci, secondary, errp)) {
        return;
    }
    s->xhci = xhci;
}

static void pcie_reset(DeviceState *dev)
{
    BCM2838PcieState *s = BCM2838_PCIE(dev);
    s->reset = BRIDGE_RESET | PERST;
    clear_controller(s);
}

static int pcie_pre_save(void *opaque)
{
    BCM2838PcieState *s = opaque;
    s->migration_connected = s->connected_xhci;
    return 0;
}

static int pcie_post_load(void *opaque, int version)
{
    BCM2838PcieState *s = opaque;
    if (s->migration_connected != s->connected_xhci) {
        return -EINVAL;
    }
    update_regions(s);
    update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_pcie = {
    .name = TYPE_BCM2838_PCIE, .version_id = 2, .minimum_version_id = 1,
    .pre_save = pcie_pre_save, .post_load = pcie_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2838PcieState, BCM2838_PCIE_NREGS),
        VMSTATE_UINT32(reset, BCM2838PcieState),
        VMSTATE_UINT32(config_index, BCM2838PcieState),
        VMSTATE_UINT32_ARRAY(status, BCM2838PcieState, 2),
        VMSTATE_UINT32_ARRAY(mask, BCM2838PcieState, 2),
        VMSTATE_BOOL_V(migration_connected, BCM2838PcieState, 2),
        VMSTATE_BOOL_ARRAY_V(intx_level, BCM2838PcieState, 4, 2),
        VMSTATE_END_OF_LIST()
    }
};

static void pcie_init(Object *obj)
{
    BCM2838PcieState *s = BCM2838_PCIE(obj);
    memory_region_init_io(&s->mmio, obj, &pcie_ops, s, TYPE_BCM2838_PCIE, 0x9310);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);
    memory_region_init(&s->cpu_mmio, obj, "bcm2711-pcie-cpu-windows", UINT64_MAX);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->cpu_mmio);
    memory_region_init(&s->pci_mmio, obj, "bcm2711-pcie-bus-mmio", UINT64_MAX);
    memory_region_init(&s->pci_io, obj, "bcm2711-pcie-bus-io", 65536);
    memory_region_init(&s->dma_memory, obj, "bcm2711-pcie-dma-windows", UINT64_MAX);
    for (int n = 0; n < 4; n++) {
        g_autofree char *name = g_strdup_printf("bcm2711-pcie-outbound%d", n);
        memory_region_init_alias(&s->outbound[n], obj, name, &s->pci_mmio, 0, 1);
        memory_region_set_enabled(&s->outbound[n], false);
        memory_region_add_subregion_overlap(&s->cpu_mmio, 0, &s->outbound[n], n);
    }
    for (int n = 0; n < 6; n++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[n]);
    }
}

static const Property pcie_props[] = {
    DEFINE_PROP_BOOL("connected-xhci", BCM2838PcieState, connected_xhci, false),
    DEFINE_PROP_LINK("ram", BCM2838PcieState, ram, TYPE_MEMORY_REGION, MemoryRegion *),
};

static void pcie_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    PCIHostBridgeClass *hc = PCI_HOST_BRIDGE_CLASS(klass);
    dc->realize = pcie_realize;
    device_class_set_props(dc, pcie_props);
    device_class_set_legacy_reset(dc, pcie_reset);
    dc->vmsd = &vmstate_pcie;
    hc->root_bus_path = pcie_root_bus_path;
}

static const TypeInfo pcie_info = {
    .name = TYPE_BCM2838_PCIE, .parent = TYPE_PCI_HOST_BRIDGE,
    .instance_size = sizeof(BCM2838PcieState),
    .instance_init = pcie_init, .class_init = pcie_class_init,
};

static void pcie_register(void)
{
    type_register_static(&pcie_info);
}
type_init(pcie_register)
