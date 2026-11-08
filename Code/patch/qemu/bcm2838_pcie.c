/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * BCM2711 PCIe controller, disconnected-port profile.
 *
 * This supplies control/reset/interrupt register semantics for SD firmware
 * bootstrap. It never reports PHY or data-link up, and every PCI config read
 * reports an absent device. There is no PCI bus, endpoint, MMIO/DMA translation,
 * MSI delivery from devices, PHY/MDIO model or PCIe timing model.
 *
 * Register definitions: Linux v6.12 drivers/pci/controller/pcie-brcmstb.c
 * and EDK2 Silicon/Broadcom/Bcm27xx/Include/IndustryStandard/Bcm2711.h.
 */
#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/irq.h"
#include "hw/misc/bcm2838_pcie.h"
#include "migration/vmstate.h"

#define SW_INIT 0x9210
#define BRIDGE_RESET 2
#define PERST 1
#define PORT_STATUS 0x4068
#define REVISION 0x406c

/* Only these control/window registers are implemented, with readback. Window
 * settings do not enable a data plane in the disconnected profile. */
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

static void update_irq(BCM2838PcieState *s)
{
    for (int n = 0; n < 2; n++) {
        qemu_set_irq(s->irq[n], !(s->reset & BRIDGE_RESET) &&
                     (s->status[n] & ~s->mask[n]));
    }
}

static void clear_controller(BCM2838PcieState *s)
{
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->status, 0, sizeof(s->status));
    s->config_index = 0;
    s->mask[0] = s->mask[1] = UINT32_MAX;
    update_irq(s);
}

static int interrupt_bank(hwaddr offset, hwaddr *reg)
{
    if (offset >= 0x4300 && offset <= 0x4314) {
        *reg = offset - 0x4300;
        return 0;
    }
    if (offset >= 0x4500 && offset <= 0x4514) {
        *reg = offset - 0x4500;
        return 1;
    }
    return -1;
}

static bool config_address(hwaddr off)
{
    return off < 0x1000 || (off >= 0x8000 && off < 0x9000);
}

static MemTxResult pcie_read(void *opaque, hwaddr off, uint64_t *data,
                             unsigned size, MemTxAttrs attrs)
{
    BCM2838PcieState *s = opaque;
    hwaddr reg;
    int n = slot(off), bank = interrupt_bank(off, &reg);

    if (size != 4 || (off & 3)) {
        if (config_address(off)) {
            *data = UINT32_MAX;
            return MEMTX_OK;
        }
        return MEMTX_ERROR;
    }
    if (off == SW_INIT) {
        *data = s->reset;
    } else if (off == PORT_STATUS) {
        /* The port is strapped as a root complex. No endpoint is connected. */
        *data = 0x80;
    } else if (off == REVISION) {
        *data = 0x0320; /* Explicit v3.20 controller profile. */
    } else if (bank >= 0) {
        *data = reg < 0x0c ? s->status[bank] : s->mask[bank];
    } else if (n >= 0) {
        *data = s->regs[n];
    } else if (off == 0x9000) {
        /* No config address can select a connected device. */
        *data = s->config_index;
    } else if (config_address(off)) {
        *data = UINT32_MAX;
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
    hwaddr reg;
    int n = slot(off), bank = interrupt_bank(off, &reg);

    if (size != 4 || (off & 3)) {
        return config_address(off) ? MEMTX_OK : MEMTX_ERROR;
    }
    if (off == SW_INIT) {
        uint32_t old = s->reset;
        s->reset = value & (BRIDGE_RESET | PERST);
        if ((s->reset & BRIDGE_RESET) && !(old & BRIDGE_RESET)) {
            clear_controller(s);
        }
    } else if (off == PORT_STATUS || off == REVISION) {
        /* Read-only straps and identification. */
    } else if (bank >= 0) {
        switch (reg) {
        case 0x00: case 0x0c: break; /* Read-only status. */
        case 0x04: s->status[bank] |= value; break;
        case 0x08: s->status[bank] &= ~value; break;
        case 0x10: s->mask[bank] |= value; break;
        case 0x14: s->mask[bank] &= ~value; break;
        }
    } else if (n >= 0) {
        if (!(s->reset & BRIDGE_RESET)) {
            s->regs[n] = value;
        }
    } else if (off == 0x9000) {
        s->config_index = value & 0x0ffff000;
    } else if (config_address(off)) {
        /* PCI configuration of an absent device cannot change any state. */
    } else {
        qemu_log_mask(LOG_UNIMP, "bcm2711-pcie: unsupported write 0x%" HWADDR_PRIx "\n", off);
        return MEMTX_ERROR;
    }
    update_irq(s);
    return MEMTX_OK;
}

static const MemoryRegionOps pcie_ops = {
    .read_with_attrs = pcie_read,
    .write_with_attrs = pcie_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 1, .valid.max_access_size = 4,
    .impl.min_access_size = 1, .impl.max_access_size = 4,
};

static void pcie_reset(DeviceState *dev)
{
    BCM2838PcieState *s = BCM2838_PCIE(dev);
    s->reset = BRIDGE_RESET | PERST;
    clear_controller(s);
}

static int pcie_post_load(void *opaque, int version)
{
    update_irq(opaque);
    return 0;
}

static const VMStateDescription vmstate_pcie = {
    .name = TYPE_BCM2838_PCIE, .version_id = 1, .minimum_version_id = 1,
    .post_load = pcie_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2838PcieState, BCM2838_PCIE_NREGS),
        VMSTATE_UINT32(reset, BCM2838PcieState),
        VMSTATE_UINT32(config_index, BCM2838PcieState),
        VMSTATE_UINT32_ARRAY(status, BCM2838PcieState, 2),
        VMSTATE_UINT32_ARRAY(mask, BCM2838PcieState, 2),
        VMSTATE_END_OF_LIST()
    }
};

static void pcie_init(Object *obj)
{
    BCM2838PcieState *s = BCM2838_PCIE(obj);
    memory_region_init_io(&s->mmio, obj, &pcie_ops, s,
                          TYPE_BCM2838_PCIE, 0x9310);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);
    for (int n = 0; n < 2; n++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[n]);
    }
}

static void pcie_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    device_class_set_legacy_reset(dc, pcie_reset);
    dc->vmsd = &vmstate_pcie;
}

static const TypeInfo pcie_info = {
    .name = TYPE_BCM2838_PCIE, .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2838PcieState),
    .instance_init = pcie_init, .class_init = pcie_class_init,
};

static void pcie_register(void)
{
    type_register_static(&pcie_info);
}
type_init(pcie_register)
