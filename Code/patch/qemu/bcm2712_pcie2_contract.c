/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Synthetic selected BCM2712 PCIe2 protocol contract on a BCM2711 boot substrate.
 * Reuses the qualified controller's real QEMU PCI/root-port/xHCI integration;
 * it does not model a Pi500, RP1 identity, electrical PHY, or analog calibration.
 * Selected register facts: raspberrypi/linux2569533d6c33c15231fbc21704c87fcebc67173c
 * drivers/pci/controller/pcie-brcmstb.c and drivers/irqchip/irq-bcm2712-mip.c.
 * Unsupported accesses/PHY tuples are not blanket register RAM or success.
 * Edge-host MIP messages0..63, masked-edge drop and zero post-edge status are
 * explicit synthetic policies; invalid data, level/VPU delivery are unsupported.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "hw/pci/pci_bridge.h"
#include "hw/pci/pci_bus.h"
#include "hw/pci/pcie_port.h"
#include "hw/misc/bcm2712_pcie2_contract.h"
#include "migration/vmstate.h"

#define BRIDGE_RESET 2
#define PORT_STATUS 0x4068
#define REVISION 0x406c

static const hwaddr offsets[BCM2712_PCIE2_CONTRACT_NREGS] = {
    0x188, 0x43c, 0x4dc, 0x4f8, 0x4008, 0x4044,
    0x4048, 0x404c, 0x405c, 0x4064, 0x184c, 0x40a0,
    0x40a4, 0x40a8, 0x4164, 0x4168, 0x416c, 0x4170,
    0x4304, 0xa0c, 0xa20, 0x400c, 0x4010, 0x4014,
    0x4018, 0x401c, 0x4020, 0x4024, 0x4028, 0x4070,
    0x4074, 0x4078, 0x407c, 0x4080, 0x4084, 0x4088,
    0x408c, 0x4090, 0x4094, 0x4098, 0x409c, 0x402c,
    0x4030, 0x4034, 0x4038, 0x403c, 0x4040, 0x40d4,
    0x40d8, 0x40dc, 0x40e0, 0x40e4, 0x40e8, 0x40ec,
    0x40f0, 0x40f4, 0x40f8, 0x40fc, 0x4100, 0x4104,
    0x4108, 0x40ac, 0x40b0, 0x40b4, 0x40b8, 0x40bc,
    0x40c0, 0x410c, 0x4110, 0x4114, 0x4118, 0x411c,
    0x4120, 0x4124, 0x4128, 0x412c, 0x4130, 0x4134,
    0x4138, 0x413c, 0x4140,
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

static uint32_t reg(BCM2712Pcie2ContractState *s, hwaddr offset)
{
    int n = slot(offset);
    assert(n >= 0);
    return s->regs[n];
}

static bool link_up(BCM2712Pcie2ContractState *s)
{
    return s->connected_xhci && !(s->reset & BRIDGE_RESET) &&
           (reg(s, 0x4064) & 4) && !(reg(s, 0x4304) & (1u << 27)) &&
           s->calibrated && s->mdio_programmed == 0x7f &&
           (s->gpio_data[0] & 4) && !(s->gpio_dir[0] & 4) &&
           !(s->pin_mux & 0xf00);
}

static void update_irq(BCM2712Pcie2ContractState *s)
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
    if (code >= 1 && code <= 0x15) {
        return 1ULL << (code + 15);
    }
    if (code >= 0x1c && code <= 0x1f) {
        return 1ULL << (code - 0x1c + 12);
    }
    return 0; /* disabled or reserved */
}

static void update_regions(BCM2712Pcie2ContractState *s)
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
    /* Three separate routes. Unknown target geometry is never RAM. */
    for (int n = 0; n < 3; n++) {
        uint32_t bar = 0x402c + n * 8, ubus = 0x40ac + n * 8;
        uint64_t target = ((uint64_t)reg(s, ubus + 4) << 32) |
                          (reg(s, ubus) & ~0xfffU);
        pci_base = ((uint64_t)reg(s, bar + 4) << 32) | (reg(s, bar) & ~31U);
        size = inbound_size(reg(s, bar) & 31);
        memory_region_set_enabled(&s->inbound[n], false);
        bool supported = (n == 0 && target == 0x1f00000000ULL && size == 0x400000) ||
                         (n == 1 && target == 0 && size == 0x1000000000ULL) ||
                         (n == 2 && target == 0x1000130000ULL && size == 0x1000);
        if (supported && (reg(s, ubus) & 1) && pci_base <= UINT64_MAX - (size - 1)) {
            memory_region_set_size(&s->inbound[n], size);
            memory_region_set_address(&s->inbound[n], pci_base);
            memory_region_set_enabled(&s->inbound[n], enabled);
        }
    }
    memory_region_transaction_commit();
}

static void clear_controller(BCM2712Pcie2ContractState *s)
{
    memset(s->regs, 0, sizeof(s->regs));
    if (s->connected_xhci) {
        /* Disabled reset windows have base above limit. Do not map untouched
         * windows over low ARM RAM when software enables SCB access. */
        for (int n = 0; n < 4; n++) {
            s->regs[slot(0x4070 + n * 4)] = 0x0000fff0;
        }
    }
    s->mdio_addr = s->mdio_write = s->mdio_read = 0;
    s->mdio_bank = s->mdio_ssc_control = 0;
    s->mdio_programmed = 0;
    memset(s->mdio_table, 0, sizeof(s->mdio_table));
    s->regs[slot(0x4304)] = 1U << 27;
    memset(s->status, 0, sizeof(s->status));
    memset(s->intx_level, 0, sizeof(s->intx_level));
    s->config_index = 0;
    s->mask[0] = s->mask[1] = UINT32_MAX;
    update_regions(s);
    update_irq(s);
}

static int interrupt_bank(hwaddr offset, hwaddr *regoff)
{
    if (offset >= 0x4400 && offset <= 0x4414) {
        *regoff = offset - 0x4400;
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

static PCIDevice *config_device(BCM2712Pcie2ContractState *s, hwaddr off)
{
    if ((s->reset & BRIDGE_RESET) || (off >= 0x8000 && !link_up(s))) {
        return NULL;
    }
    if (off < 0x1000) {
        return s->root_port;
    }
    return pci_find_device(s->parent_obj.bus, (s->config_index >> 20) & 0xff,
                           (s->config_index >> 12) & 0xff);
}

static uint32_t config_read(BCM2712Pcie2ContractState *s, hwaddr off, unsigned size)
{
    PCIDevice *d = config_device(s, off);
    return d ? pci_host_config_read_common(d, off & 0xfff, 4096, size) :
               UINT32_MAX;
}

static void config_write(BCM2712Pcie2ContractState *s, hwaddr off, uint32_t value,
                         unsigned size)
{
    PCIDevice *d = config_device(s, off);
    if (d) {
        pci_host_config_write_common(d, off & 0xfff, 4096, value, size);
    }
}

/* The selected internal PHY profile implements the native SSC setup protocol.
 * Register contract: U-Boot v2025.10/e50b1e8715011def8aff1588081a2649a2c6cd47
 * drivers/pci/pcie_brcmstb.c:210-286 (GPL-2.0, Broadcom/Samsung/Nicolas Saenz
 * Julienne), and its arch/arm/mach-bcm283x/include/mach/acpi/bcm2711.h.
 * This is a stable simulated PLL/SSC state machine, not analog timing fidelity.
 */
#define MDIO_DONE (1U << 31)

static bool mdio_clock_ready(BCM2712Pcie2ContractState *s)
{
    return !(s->reset & BRIDGE_RESET) && s->calibrated &&
           !(reg(s, 0x4304) & (1u << 27));
}

static int mdio_index(unsigned address)
{
    static const unsigned addresses[] = {0x16,0x17,0x18,0x19,0x1b,0x1c,0x1e};
    for (int n = 0; n < ARRAY_SIZE(addresses); n++) {
        if (address == addresses[n]) { return n; }
    }
    return -1;
}
static uint16_t mdio_phy_read(BCM2712Pcie2ContractState *s)
{
    unsigned address = s->mdio_addr & 0xffff;
    int n = mdio_index(address);
    if (s->mdio_addr & 0xf0000) { return 0xffff; }
    if (address == 0x1f) { return s->mdio_bank; }
    return s->mdio_bank == 0x1600 && n >= 0 ? s->mdio_table[n] : 0xffff;
}
static void mdio_phy_write(BCM2712Pcie2ContractState *s, uint16_t value)
{
    static const uint16_t expected[] = {0x50b9,0xbda1,0x94,0x97b4,0x5030,0x5030,7};
    unsigned address = s->mdio_addr & 0xffff;
    int n = mdio_index(address);
    if (s->mdio_addr & 0xf0000) { return; }
    if (address == 0x1f) { s->mdio_bank = value; }
    else if (s->mdio_bank == 0x1600 && n >= 0) {
        s->mdio_table[n] = value;
        if (value == expected[n]) { s->mdio_programmed |= 1U << n; }
        else { s->mdio_programmed &= ~(1U << n); }
    }
}

/* Selected register protocols, not a Pi500 board or an analog PHY model.
 * MIP register/message contract: raspberrypi/linux2569533d6c33c15231fbc21704c87fcebc67173c,
 * drivers/irqchip/irq-bcm2712-mip.c (GPL-2.0-only).
 * State transitions below are new QEMU implementation; no Linux code imported.
 */
static MemTxResult provider_read(void *opaque, hwaddr off, uint64_t *value,
                                  unsigned size, MemTxAttrs attrs)
{
    BCM2712ContractProvider *p = opaque;
    BCM2712Pcie2ContractState *s = p->s;
    if (size != 4 || (off & 3)) { return MEMTX_ERROR; }
    if (p->kind == 0) {
        if (off == 0 || off == 0x10 || off == 0x80 || off == 0x90 ||
            off == 0xa0 || off == 0xb0) { *value = 0; }
        else if (off == 0x20 || off == 0x30) { *value = s->mip_config[off == 0x30]; }
        else if (off == 0x40 || off == 0x50) { *value = s->mip_host_mask[off == 0x50]; }
        else if (off == 0x60 || off == 0x70) { *value = s->mip_vpu_mask[off == 0x70]; }
        else { return MEMTX_ERROR; }
    } else if (p->kind == 1) {
        if (off == 0) { *value = s->rescal_start; }
        else if (off == 8) { *value = s->rescal_status; }
        else { return MEMTX_ERROR; }
    } else if (p->kind == 2) {
        unsigned bank = off / 0x18, r = off % 0x18;
        if (bank > 1 || (r != 0 && r != 4 && r != 8)) { return MEMTX_ERROR; }
        *value = r == 8 ? s->sw_init[bank] : 0;
    } else if (p->kind == 3) {
        unsigned bank = off / 32, r = off % 32;
        if (bank > 1) { return MEMTX_ERROR; }
        if (r == 4) { *value = s->gpio_data[bank]; }
        else if (r == 8) { *value = s->gpio_dir[bank]; }
        else if (r == 0x14) { *value = s->gpio_mask[bank]; }
        else { return MEMTX_ERROR; }
    } else {
        if (off == 0xc) { *value = s->pin_mux; }
        else if (off == 0x14) { *value = s->pin_bias; }
        else { return MEMTX_ERROR; }
    }
    return MEMTX_OK;
}

static MemTxResult provider_write(void *opaque, hwaddr off, uint64_t value,
                                   unsigned size, MemTxAttrs attrs)
{
    BCM2712ContractProvider *p = opaque;
    BCM2712Pcie2ContractState *s = p->s;
    if (size != 4 || (off & 3)) { return MEMTX_ERROR; }
    if (p->kind == 0) {
        if (off == 0) {
            /* Bounded edge-host profile: message data selects a source.
             * No VPU or level-mode behavior is fabricated. Masked edges drop.
             */
            if (value >= 64) { return MEMTX_ERROR; }
            unsigned bank = value / 32, bit = value % 32;
            if (!(s->mip_config[bank] & (1U << bit)) ||
                !(s->mip_vpu_mask[bank] & (1U << bit))) { return MEMTX_ERROR; }
            if (!(s->mip_host_mask[bank] & (1U << bit))) {
                qemu_irq_pulse(s->mip_irq[value]);
            }
        } else if (off == 0x10) {
            /* Edge profile has no held level to clear. */
            if (value >= 64) { return MEMTX_ERROR; }
        } else if (off == 0x20 || off == 0x30) { s->mip_config[off == 0x30] = value; }
        else if (off == 0x40 || off == 0x50) { s->mip_host_mask[off == 0x50] = value; }
        else if (off == 0x60 || off == 0x70) { s->mip_vpu_mask[off == 0x70] = value; }
        else { return MEMTX_ERROR; }
    } else if (p->kind == 1) {
        if (off != 0 || (value & ~1U)) { return MEMTX_ERROR; }
        s->rescal_start = value;
        if (value & 1) {
            /* Synchronous virtual calibration, not electrical completion. */
            s->rescal_status = 1;
            s->calibrated = true;
        }
    } else if (p->kind == 2) {
        unsigned bank = off / 0x18, r = off % 0x18;
        if (bank > 1 || (r != 0 && r != 4)) { return MEMTX_ERROR; }
        bool was_reset = s->reset & BRIDGE_RESET;
        if (r == 0) { s->sw_init[bank] |= value; }
        else { s->sw_init[bank] &= ~value; }
        if (s->sw_init[1] & (1U << 12)) { s->reset |= BRIDGE_RESET; }
        else { s->reset &= ~BRIDGE_RESET; }
        if (!was_reset && (s->reset & BRIDGE_RESET)) {
            clear_controller(s);
            if (s->root_port) { device_cold_reset(DEVICE(s->root_port)); }
        }
    } else if (p->kind == 3) {
        unsigned bank = off / 32, r = off % 32;
        uint32_t mask = bank == 0 ? 0x7fff : 0x3f;
        if (bank > 1 || (value & ~mask)) { return MEMTX_ERROR; }
        if (r == 4) { s->gpio_data[bank] = value; }
        else if (r == 8) { s->gpio_dir[bank] = value; }
        else if (r == 0x14) { s->gpio_mask[bank] = value; }
        else { return MEMTX_ERROR; }
    } else {
        if (off == 0xc && !(value & ~0xffff00U)) { s->pin_mux = value; }
        else if (off == 0x14 && !(value & ~0x3fc00000U)) { s->pin_bias = value; }
        else { return MEMTX_ERROR; }
    }
    update_regions(s);
    update_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps provider_ops = {
    .read_with_attrs = provider_read, .write_with_attrs = provider_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult pcie_read(void *opaque, hwaddr off, uint64_t *data,
                             unsigned size, MemTxAttrs attrs)
{
    BCM2712Pcie2ContractState *s = opaque;
    hwaddr regoff;
    int n = slot(off), bank = interrupt_bank(off, &regoff);

    if (size != 4 || (off & 3)) {
        if (config_address(off)) {
            *data = config_read(s, off, size);
            return MEMTX_OK;
        }
        return MEMTX_ERROR;
    }
    if (s->connected_xhci && off >= 0x1100 && off <= 0x1108) {
        *data = off == 0x1100 ? s->mdio_addr :
                off == 0x1104 ? s->mdio_write : s->mdio_read;
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
        qemu_log_mask(LOG_UNIMP, "pcie2-contract: unsupported read 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult pcie_write(void *opaque, hwaddr off, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    BCM2712Pcie2ContractState *s = opaque;
    hwaddr regoff;
    int n = slot(off), bank = interrupt_bank(off, &regoff);

    if (size != 4 || (off & 3)) {
        if (config_address(off)) {
            config_write(s, off, value, size);
            return MEMTX_OK;
        }
        return MEMTX_ERROR;
    }
    if (s->connected_xhci && off >= 0x1100 && off <= 0x1108) {
        if (off == 0x1100) {
            s->mdio_addr = value;
            s->mdio_read = 0;
            if ((value >> 20) == 1 && mdio_clock_ready(s)) {
                s->mdio_read = MDIO_DONE | mdio_phy_read(s);
            }
        } else if (off == 0x1104) {
            s->mdio_write = value;
            if ((value & MDIO_DONE) && (s->mdio_addr >> 20) == 0 &&
                mdio_clock_ready(s)) {
                mdio_phy_write(s, value);
                s->mdio_write &= ~MDIO_DONE;
            }
        } /* Read-data is read-only. */
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
        if (!(s->reset & BRIDGE_RESET)) {
            uint32_t previous = s->regs[n];
            s->regs[n] = value;
            if (off == 0x4064 && (previous & 4) && !(value & 4) && s->root_port) {
                device_cold_reset(DEVICE(s->root_port));
            }
        }
    } else if (off == 0x9000) {
        s->config_index = value & 0x0ffff000;
    } else if (config_address(off)) {
        config_write(s, off, value, size);
    } else {
        qemu_log_mask(LOG_UNIMP, "pcie2-contract: unsupported write 0x%" HWADDR_PRIx "\n", off);
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
    BCM2712Pcie2ContractState *s = opaque;
    s->intx_level[pin] = level;
    update_irq(s);
}

static AddressSpace *pcie_dma_space(PCIBus *bus, void *opaque, int devfn)
{
    BCM2712Pcie2ContractState *s = opaque;
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
    BCM2712Pcie2ContractState *s = BCM2712_PCIE2_CONTRACT(dev);
    PCIBus *secondary;
    PCIDevice *port, *xhci;

    if (!s->ram) {
        error_setg(errp, "BCM2711 PCIe requires the board RAM link");
        return;
    }
    memory_region_init_alias(&s->inbound[0], OBJECT(s), "pcie2-peer",
                             &s->cpu_mmio, 0x1f00000000ULL, 1);
    memory_region_init_alias(&s->inbound[1], OBJECT(s), "pcie2-ram", s->ram, 0, 1);
    memory_region_init_alias(&s->inbound[2], OBJECT(s), "pcie2-msi",
                             &s->provider_mr[0], 0, 1);
    for (int n = 0; n < 3; n++) {
        memory_region_set_enabled(&s->inbound[n], false);
        memory_region_add_subregion_overlap(&s->dma_memory, 0, &s->inbound[n], n);
    }
    address_space_init(&s->dma_as, &s->dma_memory, "pcie2-contract-dma");
    s->regions_ready = true;
    if (!s->connected_xhci) {
        return;
    }
    s->parent_obj.bus = pci_register_root_bus(dev, "pcie2-contract",
        pcie_set_irq, pci_swizzle_map_irq_fn, s, &s->pci_mmio, &s->pci_io,
        0, 4, TYPE_PCIE_BUS);
    s->parent_obj.bus->flags |= PCI_BUS_EXTENDED_CONFIG_SPACE;
    pci_setup_iommu(s->parent_obj.bus, &pcie_iommu_ops, s);

    port = pci_new(PCI_DEVFN(0, 0), "bcm2711-pcie-root-port");
    object_property_add_child(OBJECT(s), "root-port", OBJECT(port));
    qdev_prop_set_uint8(DEVICE(port), "chassis", 1);
    qdev_prop_set_uint16(DEVICE(port), "slot", 0);
    qdev_prop_set_bit(DEVICE(port), "hotplug", false);
    /* Selected ceiling, using real QEMU capability initialization. Target
     * speed defaults to this ceiling; endpoint negotiation stays genuine. */
    qdev_prop_set_enum(DEVICE(port), "x-speed", PCIE_LINK_SPEED_5);
    qdev_prop_set_enum(DEVICE(port), "x-width", PCIE_LINK_WIDTH_4);
    if (!pci_realize_and_unref(port, s->parent_obj.bus, errp)) {
        return;
    }
    s->root_port = port;
    secondary = pci_bridge_get_sec_bus(PCI_BRIDGE(port));
    xhci = pci_new(PCI_DEVFN(0, 0), "qemu-xhci");
    object_property_add_child(OBJECT(s), "xhci", OBJECT(xhci));
    DEVICE(xhci)->id = g_strdup("pcie2-xhci");
    if (!object_property_set_str(OBJECT(xhci), "msi", "on", errp) ||
        !object_property_set_str(OBJECT(xhci), "msix", "on", errp) ||
        !object_property_set_bool(OBJECT(xhci), "compact-mmio", false, errp)) {
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
    BCM2712Pcie2ContractState *s = BCM2712_PCIE2_CONTRACT(dev);
    s->reset = BRIDGE_RESET;
    s->sw_init[0] = 0; s->sw_init[1] = 1U << 12;
    s->rescal_start = s->rescal_status = 0;
    s->calibrated = false;
    s->pin_mux = 0; s->pin_bias = 0;
    for (int n = 0; n < 2; n++) {
        s->mip_config[n] = 0;
        s->mip_host_mask[n] = s->mip_vpu_mask[n] = UINT32_MAX;
        s->gpio_data[n] = s->gpio_mask[n] = 0;
        s->gpio_dir[n] = n == 0 ? 0x7fff : 0x3f;
    }

    clear_controller(s);
}

static int pcie_pre_save(void *opaque)
{
    BCM2712Pcie2ContractState *s = opaque;
    s->migration_connected = s->connected_xhci;
    return 0;
}

static int pcie_post_load(void *opaque, int version)
{
    BCM2712Pcie2ContractState *s = opaque;
    if (s->migration_connected != s->connected_xhci) {
        return -EINVAL;
    }
    update_regions(s);
    update_irq(s);
    return 0;
}

static bool mdio_needed(void *opaque)
{
    return ((BCM2712Pcie2ContractState *)opaque)->connected_xhci;
}

static int mdio_post_load(void *opaque, int version)
{
    BCM2712Pcie2ContractState *s = opaque;
    return s->mdio_ssc_control & ~0xc000 ? -EINVAL : 0;
}

static const VMStateDescription vmstate_pcie_mdio = {
    .name = TYPE_BCM2712_PCIE2_CONTRACT "/mdio",
    .version_id = 1,
    .minimum_version_id = 1,
    .needed = mdio_needed,
    .post_load = mdio_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(mdio_addr, BCM2712Pcie2ContractState),
        VMSTATE_UINT32(mdio_write, BCM2712Pcie2ContractState),
        VMSTATE_UINT32(mdio_read, BCM2712Pcie2ContractState),
        VMSTATE_UINT16(mdio_bank, BCM2712Pcie2ContractState),
        VMSTATE_UINT16(mdio_ssc_control, BCM2712Pcie2ContractState),
        VMSTATE_END_OF_LIST()
    }
};

static const VMStateDescription vmstate_pcie = {
    .name = TYPE_BCM2712_PCIE2_CONTRACT, .version_id = 2, .minimum_version_id = 1,
    .pre_save = pcie_pre_save, .post_load = pcie_post_load,
    /* Endpoints reconstruct active contexts with DMA in their post_load. */
    .priority = MIG_PRI_IOMMU,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2712Pcie2ContractState, BCM2712_PCIE2_CONTRACT_NREGS),
        VMSTATE_UINT32(reset, BCM2712Pcie2ContractState),
        VMSTATE_UINT16_ARRAY(mdio_table, BCM2712Pcie2ContractState, 7),
        VMSTATE_UINT8(mdio_programmed, BCM2712Pcie2ContractState),
        VMSTATE_UINT32_ARRAY(mip_config, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(mip_host_mask, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(mip_vpu_mask, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(sw_init, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32(rescal_start, BCM2712Pcie2ContractState),
        VMSTATE_UINT32(rescal_status, BCM2712Pcie2ContractState),
        VMSTATE_UINT32_ARRAY(gpio_data, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(gpio_dir, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(gpio_mask, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32(pin_mux, BCM2712Pcie2ContractState),
        VMSTATE_UINT32(pin_bias, BCM2712Pcie2ContractState),
        VMSTATE_BOOL(calibrated, BCM2712Pcie2ContractState),

        VMSTATE_UINT32(config_index, BCM2712Pcie2ContractState),
        VMSTATE_UINT32_ARRAY(status, BCM2712Pcie2ContractState, 2),
        VMSTATE_UINT32_ARRAY(mask, BCM2712Pcie2ContractState, 2),
        VMSTATE_BOOL_V(migration_connected, BCM2712Pcie2ContractState, 2),
        VMSTATE_BOOL_ARRAY_V(intx_level, BCM2712Pcie2ContractState, 4, 2),
        VMSTATE_END_OF_LIST()
    },
    .subsections = (const VMStateDescription * const[]) {
        &vmstate_pcie_mdio,
        NULL
    }
};

static void pcie_init(Object *obj)
{
    BCM2712Pcie2ContractState *s = BCM2712_PCIE2_CONTRACT(obj);
    memory_region_init_io(&s->mmio, obj, &pcie_ops, s, TYPE_BCM2712_PCIE2_CONTRACT, 0x9310);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);
    memory_region_init(&s->cpu_mmio, obj, "pcie2-contract-cpu-windows", UINT64_MAX);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->cpu_mmio);
    memory_region_init(&s->pci_mmio, obj, "pcie2-contract-bus-mmio", UINT64_MAX);
    memory_region_init(&s->pci_io, obj, "pcie2-contract-bus-io", 65536);
    memory_region_init(&s->dma_memory, obj, "pcie2-contract-dma-windows", UINT64_MAX);
    for (int n = 0; n < 4; n++) {
        g_autofree char *name = g_strdup_printf("pcie2-contract-outbound%d", n);
        memory_region_init_alias(&s->outbound[n], obj, name, &s->pci_mmio, 0, 1);
        memory_region_set_enabled(&s->outbound[n], false);
        memory_region_add_subregion_overlap(&s->cpu_mmio, 0, &s->outbound[n], n);
    }
    static const unsigned sizes[] = {0xc0,0x10,0x30,0x40,0x1c};
    for (int n = 0; n < 5; n++) {
        s->provider[n].s = s; s->provider[n].kind = n;
        memory_region_init_io(&s->provider_mr[n], obj, &provider_ops,
                              &s->provider[n], "pcie2-contract-provider", sizes[n]);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->provider_mr[n]);
    }
    s->connected_xhci = true;
    for (int n = 0; n < 6; n++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[n]);
    }
    for (int n = 0; n < 64; n++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->mip_irq[n]);
    }
}

static const Property pcie_props[] = {
    DEFINE_PROP_LINK("ram", BCM2712Pcie2ContractState, ram, TYPE_MEMORY_REGION, MemoryRegion *),
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
    .name = TYPE_BCM2712_PCIE2_CONTRACT,
    .parent = TYPE_PCI_HOST_BRIDGE,
    .instance_size = sizeof(BCM2712Pcie2ContractState),
    .instance_init = pcie_init,
    .class_init = pcie_class_init,
};

static void pcie_register(void)
{
    type_register_static(&pcie_info);
}
type_init(pcie_register)
