/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * BCM2711 GENET control path with a permanently disconnected PHY.
 *
 * MAC/reset/MDIO, interrupt banks and descriptor/configuration SRAM are modeled.
 * There is no NIC backend or guest-memory DMA, descriptor consumption, packet
 * reception/transmission, carrier, autonegotiation success or PHY timing model.
 * Unknown MMIO is a transaction error; this is not blanket register RAM.
 * Definitions: Linux v6.12 bcmgenet.h/brcmphy.h and EDK2 BcmGenetDxe.h.
 */
#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/irq.h"
#include "hw/misc/bcm2838_genet.h"
#include "migration/vmstate.h"

#define MDIO_BUSY (1u << 29)
#define MDIO_FAIL (1u << 28)
#define MDIO_DONE_IRQ (1u << 23)
#define MDIO_ERROR_IRQ (1u << 24)
#define MAC_RESET (1u << 13)

static const hwaddr control_offsets[GENET_CONTROL_WORDS] = {
    0x004, 0x008, 0x00c, 0x080, 0x08c, 0x09c,
    0x300, 0x314, 0x39c, 0x3b4, 0x600, 0x60c,
};

static void update_irq(BCM2838GenetState *s)
{
    for (int n = 0; n < 2; n++) {
        qemu_set_irq(s->irq[n], (s->irq_status[n] & ~s->irq_mask[n]) != 0);
    }
}

static void phy_reset(BCM2838GenetState *s)
{
    memset(s->phy, 0, sizeof(s->phy));
    memset(s->phy_aux, 0, sizeof(s->phy_aux));
    memset(s->phy_shadow, 0, sizeof(s->phy_shadow));
    s->aux_select = s->shadow_select = 0;
    s->phy[0] = 0x1140; /* Autonegotiation enabled, full duplex, 1000 Mb/s. */
    s->phy[1] = 0x7809; /* Abilities only: no link or autonegotiation completion. */
    s->phy[2] = 0x600d;
    s->phy[3] = 0x84a0; /* BCM54210-compatible control subset, explicit profile. */
    s->phy[4] = 0x01e1;
    s->phy[9] = 0x0300;
}

static uint16_t phy_read(BCM2838GenetState *s, unsigned reg)
{
    if (reg == 0x18) {
        return (s->phy_aux[s->aux_select] & 0x7ff8) | s->aux_select;
    }
    if (reg == 0x1c) {
        return (s->shadow_select << 10) | s->phy_shadow[s->shadow_select];
    }
    return s->phy[reg];
}

static void phy_write(BCM2838GenetState *s, unsigned reg, uint16_t value)
{
    if (reg == 0) {
        if (value & 0x8000) {
            phy_reset(s); /* Immediate functional reset; no timing claim. */
        } else {
            s->phy[0] = value & ~0x8200; /* Reset/restart complete immediately. */
        }
    } else if (reg == 0x18) {
        s->aux_select = value & 7;
        if (value & 0x8000) {
            s->phy_aux[s->aux_select] = value & 0x7ff8;
        }
    } else if (reg == 0x1c) {
        s->shadow_select = (value >> 10) & 31;
        if (value & 0x8000) {
            s->phy_shadow[s->shadow_select] = value & 0x03ff;
        }
    } else if (reg == 1 || reg == 2 || reg == 3 || reg == 5 || reg == 6 || reg == 10) {
        /* Read-only capabilities, identity and absent link-partner state. */
    } else {
        s->phy[reg] = value;
    }
}

static void mdio_start(BCM2838GenetState *s, uint32_t value)
{
    unsigned addr = (value >> 21) & 31, reg = (value >> 16) & 31;
    unsigned op = (value >> 26) & 3;
    s->mdio_command = value & ~(MDIO_BUSY | MDIO_FAIL);
    if (!(value & MDIO_BUSY)) {
        return;
    }
    if (addr != 1 || (op != 1 && op != 2)) {
        s->mdio_command = (s->mdio_command & ~0xffffu) | MDIO_FAIL | 0xffff;
        s->irq_status[0] |= MDIO_ERROR_IRQ;
    } else {
        if (op == 2) {
            s->mdio_command = (s->mdio_command & ~0xffffu) | phy_read(s, reg);
        } else {
            phy_write(s, reg, value);
        }
        s->irq_status[0] |= MDIO_DONE_IRQ;
    }
}

static void mac_reset(BCM2838GenetState *s)
{
    memset(s->mac, 0, sizeof(s->mac));
    memset(s->filters, 0, sizeof(s->filters));
    s->filter_control = 0;
    s->max_frame = 1518;
    s->mib_control = s->tx_flush = 0;
}

/* Known register words. Descriptor SRAM and ring metadata are separate from
 * packet memory: storing configuration never performs a guest-memory access. */
static uint32_t *word(BCM2838GenetState *s, hwaddr off)
{
    for (int n = 0; n < ARRAY_SIZE(control_offsets); n++) {
        if (off == control_offsets[n]) {
            return &s->control[n];
        }
    }
    switch (off) {
    case 0x808: return &s->mac_command;
    case 0x80c: return &s->mac[0];
    case 0x810: return &s->mac[1];
    case 0x814: return &s->max_frame;
    case 0xb34: return &s->tx_flush;
    case 0xd80: return &s->mib_control;
    case 0xe50: return &s->filter_control;
    }
    if (off >= 0xe54 && off <= 0xed8) {
        return &s->filters[(off - 0xe54) / 4];
    }
    for (int n = 0; n < 2; n++) {
        hwaddr base = n ? 0x4000 : 0x2000;
        if (off >= base && off < base + 0xc00) {
            return &s->descriptors[n][(off - base) / 4];
        }
        if (off >= base + 0xc00 && off < base + 0x1040 &&
            ((off - base - 0xc00) & 0x3f) <= 0x30) {
            return &s->rings[n][(off - base - 0xc00) / 4];
        }
        if (off == base + 0x1040) {
            return &s->dma_config[n];
        }
        if (off == base + 0x1044) {
            return &s->dma_control[n];
        }
        if (off == base + 0x104c) {
            return &s->dma_burst[n];
        }
    }
    return NULL;
}

static int irq_bank(hwaddr off, hwaddr *reg)
{
    for (int n = 0; n < 2; n++) {
        hwaddr base = n ? 0x240 : 0x200;
        if (off >= base && off <= base + 0x14) {
            *reg = off - base;
            return n;
        }
    }
    return -1;
}

static MemTxResult genet_read(void *opaque, hwaddr off, uint64_t *data,
                              unsigned size, MemTxAttrs attrs)
{
    BCM2838GenetState *s = opaque;
    hwaddr reg;
    uint32_t *ptr;
    int bank;
    if (size != 4 || (off & 3)) {
        return MEMTX_ERROR;
    }
    bank = irq_bank(off, &reg);
    ptr = word(s, off);
    if (off == 0) {
        *data = 0x06000000; /* GENET v5 major encoding. */
    } else if (off == 0xe14) {
        *data = s->mdio_command;
    } else if (bank >= 0) {
        *data = reg < 0x0c ? s->irq_status[bank] : s->irq_mask[bank];
    } else if (off == 0x3048 || off == 0x5048) {
        *data = !(s->dma_control[off == 0x5048] & 1); /* DMA_DISABLED. */
    } else if (off >= 0xc00 && off <= 0xd7c) {
        *data = 0; /* MIB counters: no packets have traversed this profile. */
    } else if (ptr) {
        *data = *ptr;
        if (off == 0x08c) {
            *data &= ~0x10u; /* PHY link cannot be fabricated by an OOB write. */
        }
    } else {
        qemu_log_mask(LOG_UNIMP, "bcm2711-genet: unsupported read 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult genet_write(void *opaque, hwaddr off, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    BCM2838GenetState *s = opaque;
    hwaddr reg;
    uint32_t *ptr;
    int bank;
    if (size != 4 || (off & 3)) {
        return MEMTX_ERROR;
    }
    bank = irq_bank(off, &reg);
    ptr = word(s, off);
    if (off == 0xe14) {
        mdio_start(s, value);
    } else if (bank >= 0) {
        switch (reg) {
        case 0x00: case 0x0c: break;
        case 0x04: s->irq_status[bank] |= value; break;
        case 0x08: s->irq_status[bank] &= ~value; break;
        case 0x10: s->irq_mask[bank] |= value; break;
        case 0x14: s->irq_mask[bank] &= ~value; break;
        }
    } else if (off == 0 || off == 0x3048 || off == 0x5048 ||
               (off >= 0xc00 && off <= 0xd7c)) {
        /* Read-only revision, DMA status and packet counters. */
    } else if (ptr) {
        if (off == 0x808 && (value & MAC_RESET)) {
            mac_reset(s);
        }
        *ptr = value;
        if (off == 0x080 && (value & (1u << 8))) {
            phy_reset(s);
        }
        if (off == 0x810) {
            *ptr &= 0xffff;
        } else if (off == 0x08c) {
            *ptr &= ~0x10u;
        }
    } else {
        qemu_log_mask(LOG_UNIMP, "bcm2711-genet: unsupported write 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    update_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps genet_ops = {
    .read_with_attrs = genet_read, .write_with_attrs = genet_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .impl.min_access_size = 4, .impl.max_access_size = 4,
};

static void genet_reset(DeviceState *dev)
{
    BCM2838GenetState *s = BCM2838_GENET(dev);
    memset(s->control, 0, sizeof(s->control));
    memset(s->descriptors, 0, sizeof(s->descriptors));
    memset(s->rings, 0, sizeof(s->rings));
    memset(s->dma_config, 0, sizeof(s->dma_config));
    memset(s->dma_control, 0, sizeof(s->dma_control));
    memset(s->dma_burst, 0, sizeof(s->dma_burst));
    memset(s->irq_status, 0, sizeof(s->irq_status));
    s->irq_mask[0] = s->irq_mask[1] = UINT32_MAX;
    s->mac_command = s->mdio_command = 0;
    mac_reset(s);
    phy_reset(s);
    update_irq(s);
}

static int genet_post_load(void *opaque, int version)
{
    BCM2838GenetState *s = opaque;
    if (s->aux_select >= 8 || s->shadow_select >= 32) {
        return -EINVAL;
    }
    update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_genet = {
    .name = TYPE_BCM2838_GENET, .version_id = 1, .minimum_version_id = 1,
    .post_load = genet_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(control, BCM2838GenetState, GENET_CONTROL_WORDS),
        VMSTATE_UINT32(mac_command, BCM2838GenetState),
        VMSTATE_UINT32_ARRAY(mac, BCM2838GenetState, 2),
        VMSTATE_UINT32(max_frame, BCM2838GenetState),
        VMSTATE_UINT32(mib_control, BCM2838GenetState),
        VMSTATE_UINT32(tx_flush, BCM2838GenetState),
        VMSTATE_UINT32(mdio_command, BCM2838GenetState),
        VMSTATE_UINT32(filter_control, BCM2838GenetState),
        VMSTATE_UINT32_ARRAY(filters, BCM2838GenetState, 34),
        VMSTATE_UINT32_ARRAY(irq_status, BCM2838GenetState, 2),
        VMSTATE_UINT32_ARRAY(irq_mask, BCM2838GenetState, 2),
        VMSTATE_UINT32_2DARRAY(descriptors, BCM2838GenetState, 2, GENET_DESCRIPTOR_WORDS),
        VMSTATE_UINT32_2DARRAY(rings, BCM2838GenetState, 2, GENET_RING_WORDS),
        VMSTATE_UINT32_ARRAY(dma_config, BCM2838GenetState, 2),
        VMSTATE_UINT32_ARRAY(dma_control, BCM2838GenetState, 2),
        VMSTATE_UINT32_ARRAY(dma_burst, BCM2838GenetState, 2),
        VMSTATE_UINT16_ARRAY(phy, BCM2838GenetState, 32),
        VMSTATE_UINT16_ARRAY(phy_aux, BCM2838GenetState, 8),
        VMSTATE_UINT16_ARRAY(phy_shadow, BCM2838GenetState, 32),
        VMSTATE_UINT8(aux_select, BCM2838GenetState),
        VMSTATE_UINT8(shadow_select, BCM2838GenetState),
        VMSTATE_END_OF_LIST()
    }
};

static void genet_init(Object *obj)
{
    BCM2838GenetState *s = BCM2838_GENET(obj);
    memory_region_init_io(&s->mmio, obj, &genet_ops, s,
                          TYPE_BCM2838_GENET, 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);
    for (int n = 0; n < 2; n++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[n]);
    }
}

static void genet_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, genet_reset);
    dc->vmsd = &vmstate_genet;
}

static const TypeInfo genet_info = {
    .name = TYPE_BCM2838_GENET, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2838GenetState),
    .instance_init = genet_init, .class_init = genet_class_init,
};

static void genet_register(void)
{
    type_register_static(&genet_info);
}
type_init(genet_register)
