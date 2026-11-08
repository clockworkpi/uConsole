/*
 * BCM2838 Gigabit Ethernet emulation
 *
 * Copyright (C) 2022 Ovchinnikov Vitalii <vitalii.ovchinnikov@auriga.com>
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/module.h"
#include "qemu/log.h"
#include "qemu/error-report.h"
#include "net/eth.h"
#include "qapi/error.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "net/checksum.h"
#include "system/dma.h"
#include "hw/registerfields.h"
#include "hw/resettable.h"
#include "migration/vmstate.h"
#include "hw/net/bcm2838_genet_net.h"
#include "trace.h"

static void bcm2838_genet_phy_update_link(BCM2838GenetNetState *s);
static void bcm2838_genet_phy_reset(BCM2838GenetNetState *s);

/* GENET layouts */
REG32(GENET_SYS_REV_CTRL,              0)
FIELD(GENET_SYS_REV_CTRL, GPHY_REV,    0, 16)
FIELD(GENET_SYS_REV_CTRL, MINOR_REV,   16, 4)
FIELD(GENET_SYS_REV_CTRL, RSVD_20_23,  20, 4)
FIELD(GENET_SYS_REV_CTRL, MAJOR_REV,   24, 4)
FIELD(GENET_SYS_REV_CTRL, RSVD_28_31,  28, 4)

REG32(GENET_INTRL_0,                0)
FIELD(GENET_INTRL_0, SCB,           0, 1)
FIELD(GENET_INTRL_0, EPHY,          1, 1)
FIELD(GENET_INTRL_0, PHY_DET_R,     2, 1)
FIELD(GENET_INTRL_0, PHY_DET_F,     3, 1)
FIELD(GENET_INTRL_0, LINK_UP,       4, 1)
FIELD(GENET_INTRL_0, LINK_DOWN,     5, 1)
FIELD(GENET_INTRL_0, UMAC,          6, 1)
FIELD(GENET_INTRL_0, UMAC_TSV,      7, 1)
FIELD(GENET_INTRL_0, TBUF_UNDERRUN, 8, 1)
FIELD(GENET_INTRL_0, RBUF_OVERFLOW, 9, 1)
FIELD(GENET_INTRL_0, HFB_SM,        10, 1)
FIELD(GENET_INTRL_0, HFB_MM,        11, 1)
FIELD(GENET_INTRL_0, MPD_R,         12, 1)
FIELD(GENET_INTRL_0, RXDMA_MBDONE,  13, 1)
FIELD(GENET_INTRL_0, RXDMA_PDONE,   14, 1)
FIELD(GENET_INTRL_0, RXDMA_BDONE,   15, 1)
FIELD(GENET_INTRL_0, TXDMA_MBDONE,  16, 1)
FIELD(GENET_INTRL_0, TXDMA_PDONE,   17, 1)
FIELD(GENET_INTRL_0, TXDMA_BDONE,   18, 1)
FIELD(GENET_INTRL_0, RSVD_19_22,    19, 4)
FIELD(GENET_INTRL_0, MDIO_DONE,     23, 1)
FIELD(GENET_INTRL_0, MDIO_ERROR,    24, 1)
FIELD(GENET_INTRL_0, RSVD_25_31,    25, 4)

REG32(GENET_INTRL_1,              0)
FIELD(GENET_INTRL_1, TX_INTRS,    0, 16)
FIELD(GENET_INTRL_1, RX_INTRS,    16, 16)

REG32(GENET_UMAC_CMD,                  0)
FIELD(GENET_UMAC_CMD, TX_EN,           0, 1)
FIELD(GENET_UMAC_CMD, RX_EN,           1, 1)
FIELD(GENET_UMAC_CMD, SPEED,           2, 2)
FIELD(GENET_UMAC_CMD, PROMISC,         4, 1)
FIELD(GENET_UMAC_CMD, PAD_EN,          5, 1)
FIELD(GENET_UMAC_CMD, CRC_FWD,         6, 1)
FIELD(GENET_UMAC_CMD, PAUSE_FWD,       7, 1)
FIELD(GENET_UMAC_CMD, RX_PAUSE_IGNORE, 8, 1)
FIELD(GENET_UMAC_CMD, TX_ADDR_INS,     9, 1)
FIELD(GENET_UMAC_CMD, HD_EN,           10, 1)
FIELD(GENET_UMAC_CMD, SW_RESET_OLD,    11, 1)
FIELD(GENET_UMAC_CMD, RSVD_12,         12, 1)
FIELD(GENET_UMAC_CMD, SW_RESET,        13, 1)
FIELD(GENET_UMAC_CMD, RSVD_14,         14, 1)
FIELD(GENET_UMAC_CMD, LCL_LOOP_EN,     15, 1)
FIELD(GENET_UMAC_CMD, RSVD_16_21,      16, 6)
FIELD(GENET_UMAC_CMD, AUTO_CONFIG,     22, 1)
FIELD(GENET_UMAC_CMD, CNTL_FRM_EN,     23, 1)
FIELD(GENET_UMAC_CMD, NO_LEN_CHK,      24, 1)
FIELD(GENET_UMAC_CMD, RMT_LOOP_EN,     25, 1)
FIELD(GENET_UMAC_CMD, RX_ERR_DISC,     26, 1)
FIELD(GENET_UMAC_CMD, PRBL_EN,         27, 1)
FIELD(GENET_UMAC_CMD, TX_PAUSE_IGNORE, 28, 1)
FIELD(GENET_UMAC_CMD, TX_RX_EN,        29, 1)
FIELD(GENET_UMAC_CMD, RUNT_FILTER_DIS, 30, 1)
FIELD(GENET_UMAC_CMD, RSVD_31,         31, 1)

REG32(GENET_UMAC_MAC_0,         0)
FIELD(GENET_UMAC_MAC_0, ADDR_3, 0,  8)
FIELD(GENET_UMAC_MAC_0, ADDR_2, 8,  8)
FIELD(GENET_UMAC_MAC_0, ADDR_1, 16, 8)
FIELD(GENET_UMAC_MAC_0, ADDR_0, 24, 8)

REG32(GENET_UMAC_MAC_1,             0)
FIELD(GENET_UMAC_MAC_1, ADDR_5,     0,  8)
FIELD(GENET_UMAC_MAC_1, ADDR_4,     8,  8)
FIELD(GENET_UMAC_MAC_1, RSVD_16_31, 16, 8)

REG32(GENET_UMAC_MDIO_CMD,             0)
FIELD(GENET_UMAC_MDIO_CMD, REG_DATA,   0, 16)
FIELD(GENET_UMAC_MDIO_CMD, REG_ID,     16, 5)
FIELD(GENET_UMAC_MDIO_CMD, PHY_ID,     21, 5)
FIELD(GENET_UMAC_MDIO_CMD, WR,         26, 1)
FIELD(GENET_UMAC_MDIO_CMD, RD,         27, 1)
FIELD(GENET_UMAC_MDIO_CMD, RD_FAIL,    28, 1)
FIELD(GENET_UMAC_MDIO_CMD, START_BUSY, 29, 1)
FIELD(GENET_UMAC_MDIO_CMD, RSVD_30_31, 30, 2)

REG32(GENET_DMA_RING_CFG,             0)
FIELD(GENET_DMA_RING_CFG, EN,         0, 17)
FIELD(GENET_DMA_RING_CFG, RSVD_17_31, 17, 14)

REG32(GENET_DMA_CTRL,              0)
FIELD(GENET_DMA_CTRL, EN,          0, 1)
FIELD(GENET_DMA_CTRL, RING_BUF_EN, 1, 17)
FIELD(GENET_DMA_CTRL, RSVD_18_19,  18, 2)
FIELD(GENET_DMA_CTRL, TSB_SWAP_EN, 20, 1)
FIELD(GENET_DMA_CTRL, RSVD_21_31,  21, 11)

REG32(GENET_DMA_PROD_INDEX,              0)
FIELD(GENET_DMA_PROD_INDEX, INDEX,       0, 16)
FIELD(GENET_DMA_PROD_INDEX, DISCARD_CNT, 16, 16)

REG32(GENET_DMA_CONS_INDEX,             0)
FIELD(GENET_DMA_CONS_INDEX, INDEX,      0, 16)
FIELD(GENET_DMA_CONS_INDEX, RSVD_16_31, 16, 16)

REG32(GENET_DMA_STATUS,                     0)
FIELD(GENET_DMA_STATUS, DISABLED,           0, 1)
FIELD(GENET_DMA_STATUS, DESC_RAM_INIT_BUSY, 1, 1)
FIELD(GENET_DMA_STATUS, RSVD_2_31,          2, 30)

#define GENET_DMA_ENABLE_MASK ((1U << 18) - 1)

REG32(GENET_RDMA_LENGTH_STATUS,             0)
FIELD(GENET_RDMA_LENGTH_STATUS, OVERRUN,    0, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, CRC_ERROR,  1, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, RXERR,      2, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, NO,         3, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, LG,         4, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, MULTICAST,  5, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, BROADCAST,  6, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, RSVD_7_11,  7, 5)
FIELD(GENET_RDMA_LENGTH_STATUS, WRAP,       12, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, SOP,        13, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, EOP,        14, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, OWN,        15, 1)
FIELD(GENET_RDMA_LENGTH_STATUS, BUFLENGTH,  16, 12)
FIELD(GENET_RDMA_LENGTH_STATUS, RSVD_28_31, 29, 4)

REG32(GENET_TDMA_LENGTH_STATUS,             0)
FIELD(GENET_TDMA_LENGTH_STATUS, RSVD_0_3,   0, 4)
FIELD(GENET_TDMA_LENGTH_STATUS, DO_CSUM,    4, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, OW_CRC,     5, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, APPEND_CRC, 6, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, RSVD_7_8,   7, 2)
FIELD(GENET_TDMA_LENGTH_STATUS, UNDERRUN,   9, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, RSVD_10_11, 10, 2)
FIELD(GENET_TDMA_LENGTH_STATUS, WRAP,       12, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, SOP,        13, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, EOP,        14, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, OWN,        15, 1)
FIELD(GENET_TDMA_LENGTH_STATUS, BUFLENGTH,  16, 12)
FIELD(GENET_TDMA_LENGTH_STATUS, RSVD_28_31, 29, 4)

REG16(GENET_PHY_BMCR,            0)
FIELD(GENET_PHY_BMCR, RSVD_0_5,  0, 6)
FIELD(GENET_PHY_BMCR, SPEED1000, 6, 1)
FIELD(GENET_PHY_BMCR, CTST,      7, 1)
FIELD(GENET_PHY_BMCR, FULLDPLX,  8, 1)
FIELD(GENET_PHY_BMCR, ANRESTART, 9, 1)
FIELD(GENET_PHY_BMCR, ISOLATE,   10, 1)
FIELD(GENET_PHY_BMCR, PDOWN,     11, 1)
FIELD(GENET_PHY_BMCR, AENABLE,   12, 1)
FIELD(GENET_PHY_BMCR, SPEED100,  13, 1)
FIELD(GENET_PHY_BMCR, LOOPBACK,  14, 1)
FIELD(GENET_PHY_BMCR, RESET,     15, 1)

REG16(GENET_PHY_BMSR,               0)
FIELD(GENET_PHY_BMSR, ERCAP,        0, 1)
FIELD(GENET_PHY_BMSR, JCD,          1, 1)
FIELD(GENET_PHY_BMSR, LSTATUS,      2, 1)
FIELD(GENET_PHY_BMSR, ANEGCAPABLE,  3, 1)
FIELD(GENET_PHY_BMSR, RFAULT,       4, 1)
FIELD(GENET_PHY_BMSR, ANEGCOMPLETE, 5, 1)
FIELD(GENET_PHY_BMSR, RSVD_6_7,     6, 2)
FIELD(GENET_PHY_BMSR, ESTATEN,      8, 1)
FIELD(GENET_PHY_BMSR, _100HALF2,    9, 1)
FIELD(GENET_PHY_BMSR, _100FULL2,    10, 1)
FIELD(GENET_PHY_BMSR, _10HALF,      11, 1)
FIELD(GENET_PHY_BMSR, _10FULL,      12, 1)
FIELD(GENET_PHY_BMSR, _100HALF,     13, 1)
FIELD(GENET_PHY_BMSR, _100FULL,     14, 1)
FIELD(GENET_PHY_BMSR, _10BASE4,     15, 1)

REG16(GENET_PHY_LPA,                           0)
FIELD(GENET_PHY_LPA, SLCT,                     0, 5)
FIELD(GENET_PHY_LPA, _10HALF_1000XFULL,        5, 1)
FIELD(GENET_PHY_LPA, _10FULL_1000XHALF,        6, 1)
FIELD(GENET_PHY_LPA, _100HALF_1000XPAUSE,      7, 1)
FIELD(GENET_PHY_LPA, _100FULL_1000XPAUSE_ASYM, 8, 1)
FIELD(GENET_PHY_LPA, _100BASE4,                9, 1)
FIELD(GENET_PHY_LPA, PAUSE_CAP,                10, 1)
FIELD(GENET_PHY_LPA, PAUSE_ASYM,               11, 1)
FIELD(GENET_PHY_LPA, RSVD_12,                  12, 1)
FIELD(GENET_PHY_LPA, RFAULT,                   13, 1)
FIELD(GENET_PHY_LPA, LPACK,                    14, 1)
FIELD(GENET_PHY_LPA, NPAGE,                    15, 1)

REG16(GENET_PHY_STAT_1000,            0)
FIELD(GENET_PHY_STAT_1000, RSVD_0_9,  0, 10)
FIELD(GENET_PHY_STAT_1000, HALF,      10, 1)
FIELD(GENET_PHY_STAT_1000, FULL,      11, 1)
FIELD(GENET_PHY_STAT_1000, REMRXOK,   12, 1)
FIELD(GENET_PHY_STAT_1000, LOCALRXOK, 13, 1)
FIELD(GENET_PHY_STAT_1000, MSRES,     14, 1)
FIELD(GENET_PHY_STAT_1000, MSFAIL,    15, 1)

/* There are two data representations for PHY_AUX_CTRL register */
REG16(GENET_PHY_AUX_CTRL_0,              0)
FIELD(GENET_PHY_AUX_CTRL_0, REG_ID_MASK, 0, 3)
FIELD(GENET_PHY_AUX_CTRL_0, RSVD_3,      3, 1)
FIELD(GENET_PHY_AUX_CTRL_0, REG_DATA,    4, 8)
FIELD(GENET_PHY_AUX_CTRL_0, REG_ID,      12, 3)
FIELD(GENET_PHY_AUX_CTRL_0, MISC_WREN,   15, 1)

REG16(GENET_PHY_AUX_CTRL_1,           0)
FIELD(GENET_PHY_AUX_CTRL_1, RSVD_0_3, 0, 4)
FIELD(GENET_PHY_AUX_CTRL_1, REG_DATA, 4, 12)

REG16(GENET_PHY_SHADOW,           0)
FIELD(GENET_PHY_SHADOW, REG_DATA, 0, 10)
FIELD(GENET_PHY_SHADOW, REG_ID,   10, 5)
FIELD(GENET_PHY_SHADOW, WR,       15, 1)

REG16(GENET_PHY_EXP_SEL,           0)
FIELD(GENET_PHY_EXP_SEL, REG_ID,   0, 8)
FIELD(GENET_PHY_EXP_SEL, BLOCK_ID, 8, 8)

REG32(GENET_TX_CSUM_INFO,            0)
FIELD(GENET_TX_CSUM_INFO, OFFSET,    0, 15)
FIELD(GENET_TX_CSUM_INFO, PROTO_UDP, 15, 1)
FIELD(GENET_TX_CSUM_INFO, START,     16, 15)
FIELD(GENET_TX_CSUM_INFO, LV,        30, 1)


static void bcm2838_genet_set_qemu_mac(BCM2838GenetNetState *s)
{
    const MACAddr *addr = &s->nic_conf.macaddr;

    s->regs.umac.mac0 = FIELD_DP32(s->regs.umac.mac0, GENET_UMAC_MAC_0,
                                   ADDR_0, addr->a[0]);
    s->regs.umac.mac0 = FIELD_DP32(s->regs.umac.mac0, GENET_UMAC_MAC_0,
                                   ADDR_1, addr->a[1]);
    s->regs.umac.mac0 = FIELD_DP32(s->regs.umac.mac0, GENET_UMAC_MAC_0,
                                   ADDR_2, addr->a[2]);
    s->regs.umac.mac0 = FIELD_DP32(s->regs.umac.mac0, GENET_UMAC_MAC_0,
                                   ADDR_3, addr->a[3]);
    s->regs.umac.mac1 = FIELD_DP32(s->regs.umac.mac1, GENET_UMAC_MAC_1,
                                   ADDR_4, addr->a[4]);
    s->regs.umac.mac1 = FIELD_DP32(s->regs.umac.mac1, GENET_UMAC_MAC_1,
                                   ADDR_5, addr->a[5]);
}

static void bcm2838_genet_set_irq_default(BCM2838GenetNetState *s)
{
    uint32_t intrl_0_status = s->regs.intrl0.stat;
    uint32_t intrl_0_mask = s->regs.intrl0.mask_status;
    int level = (intrl_0_status & ~intrl_0_mask) == 0 ? 0 : 1;

    qemu_set_irq(s->irq_default, level);
}

static void bcm2838_genet_set_irq_prio(BCM2838GenetNetState *s)
{
    uint32_t intrl_1_status = s->regs.intrl1.stat;
    uint32_t intrl_1_mask = s->regs.intrl1.mask_status;
    int level = (intrl_1_status & ~intrl_1_mask) == 0 ? 0 : 1;

    qemu_set_irq(s->irq_prio, level);
}

static void bcm2838_genet_phy_aux_ctl_write(BCM2838GenetNetState *s,
                                            uint16_t value)
{
    uint16_t reg_id = FIELD_EX16(value, GENET_PHY_AUX_CTRL_0, REG_ID);
    uint16_t reg_id_mask = FIELD_EX16(value, GENET_PHY_AUX_CTRL_0, REG_ID_MASK);
    uint16_t misc_wren = FIELD_EX16(value, GENET_PHY_AUX_CTRL_0, MISC_WREN);
    uint16_t reg_data = FIELD_EX16(value, GENET_PHY_AUX_CTRL_0, REG_DATA);
    uint16_t reg_data12 = FIELD_EX16(value, GENET_PHY_AUX_CTRL_1, REG_DATA);

    uint16_t *phy_aux_ctl_shd_reg_id =
        &s->phy_aux_ctl_shd_regs.regs[reg_id];
    uint16_t *phy_aux_ctl_shd_reg_id_mask =
        &s->phy_aux_ctl_shd_regs.regs[reg_id_mask];

    if (reg_id_mask == BCM2838_GENET_PHY_AUX_CTL_MISC) {
        if (reg_id == BCM2838_GENET_PHY_AUX_CTL_MISC) {
            if (misc_wren == 0) {
                /* write for subsequent read (8-bit from AUX_CTL_MISC) */
                FIELD_DP16(value, GENET_PHY_AUX_CTRL_0, REG_DATA,
                          *phy_aux_ctl_shd_reg_id);
            } else {
                /* write the 8-bit REG_DATA field to AUX_CTL_MISC */
                *phy_aux_ctl_shd_reg_id_mask = reg_data;
            }
        } else {
            /* write for subsequent read (12-bit) */
            FIELD_DP16(value, GENET_PHY_AUX_CTRL_1, REG_DATA,
                      *phy_aux_ctl_shd_reg_id);
        }
    } else {
        /* write 12 bits */
        *phy_aux_ctl_shd_reg_id_mask = reg_data12;
    }

    s->phy_regs.aux_ctl = value;
}

static void bcm2838_genet_phy_shadow_write(BCM2838GenetNetState *s,
                                           uint16_t value)
{
    uint16_t reg_id = FIELD_EX16(value, GENET_PHY_SHADOW, REG_ID);
    uint16_t wr = FIELD_EX16(value, GENET_PHY_SHADOW, WR);
    uint16_t reg_data = FIELD_EX16(value, GENET_PHY_SHADOW, REG_DATA);

    uint16_t *phy_shd_reg = (uint16_t *)&s->phy_shd_regs + reg_id;

    if (wr == 0) {
        FIELD_DP16(value, GENET_PHY_SHADOW, REG_DATA, *phy_shd_reg);
    } else {
        *phy_shd_reg = reg_data;
    }

    s->phy_regs.shd = value;
}

static void bcm2838_genet_phy_exp_shadow_write(BCM2838GenetNetState *s,
                                               uint16_t value)
{
    /*
     * TODO Stub implementation without side effect,
     * just storing registers values
     */
    uint16_t reg_id = FIELD_EX16(s->phy_regs.exp_ctrl,
                                 GENET_PHY_EXP_SEL, REG_ID);
    uint16_t block_id = FIELD_EX16(s->phy_regs.exp_ctrl,
                                   GENET_PHY_EXP_SEL, BLOCK_ID);

    assert(block_id < ARRAY_SIZE(s->phy_exp_shd_regs.regs));
    assert(reg_id < ARRAY_SIZE(s->phy_exp_shd_regs.regs[0]));
    s->phy_exp_shd_regs.regs[block_id][reg_id] = value;
}

static uint16_t bcm2838_genet_phy_exp_shadow_read(BCM2838GenetNetState *s)
{
    uint16_t reg_id = FIELD_EX16(s->phy_regs.exp_ctrl,
                                 GENET_PHY_EXP_SEL, REG_ID);
    uint16_t block_id = FIELD_EX16(s->phy_regs.exp_ctrl,
                                   GENET_PHY_EXP_SEL, BLOCK_ID);

    assert(block_id < ARRAY_SIZE(s->phy_exp_shd_regs.regs));
    assert(reg_id < ARRAY_SIZE(s->phy_exp_shd_regs.regs[0]));
    return s->phy_exp_shd_regs.regs[block_id][reg_id];
}

static uint64_t bcm2838_genet_mdio_cmd(BCM2838GenetNetState *s, uint64_t cmd)
{
    uint32_t phy_reg_id = FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, REG_ID);
    uint32_t phy_reg_data = FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, REG_DATA);
    uint32_t start_busy = FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, START_BUSY);
    uint32_t rd = FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, RD);
    uint32_t wr = FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, WR);
    uint16_t *phy_reg = (uint16_t *)&s->phy_regs + phy_reg_id;

    uint16_t anrestart = FIELD_EX16(phy_reg_data, GENET_PHY_BMCR, ANRESTART);

    if (start_busy && FIELD_EX32(cmd, GENET_UMAC_MDIO_CMD, PHY_ID) != s->phy_addr) {
        cmd = FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, START_BUSY, 0);
        cmd = FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, RD_FAIL, rd != 0);
        return FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, REG_DATA, 0xffff);
    }
    if (start_busy != 0) {
        cmd = FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, START_BUSY, 0);

        if (rd != 0) {
            if (phy_reg_id == BCM2838_GENET_EXP_DATA) {
                cmd = FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, REG_DATA,
                                 bcm2838_genet_phy_exp_shadow_read(s));
            } else {
                cmd = FIELD_DP32(cmd, GENET_UMAC_MDIO_CMD, REG_DATA, *phy_reg);
            }
        } else if (wr != 0) {
            /* Clause22 status/identity/partner abilities are read-only. */
            if (phy_reg_id == 1 || phy_reg_id == 2 || phy_reg_id == 3 ||
                phy_reg_id == 5 || phy_reg_id == 6 || phy_reg_id == 10 ||
                phy_reg_id == 15 || phy_reg_id == 25) {
                return cmd;
            }
            if (phy_reg_id == 0 && (phy_reg_data & 0x8000)) {
                bcm2838_genet_phy_reset(s);
                return cmd;
            }
            if (phy_reg_id == BCM2838_GENET_PHY_AUX_CTL) {
                bcm2838_genet_phy_aux_ctl_write(s, phy_reg_data);
            } else if (phy_reg_id == BCM2838_GENET_PHY_SHD) {
                bcm2838_genet_phy_shadow_write(s, phy_reg_data);
            } else if (phy_reg_id == BCM2838_GENET_EXP_DATA) {
                bcm2838_genet_phy_exp_shadow_write(s, phy_reg_data);
            } else {
                if (phy_reg_id == BCM2838_GENET_PHY_BMCR) {
                    /* Initiate auto-negotiation once it has been restarted */
                    if (anrestart == 1) {
                        phy_reg_data = FIELD_DP16(phy_reg_data,
                                                  GENET_PHY_BMCR,
                                                  ANRESTART, 0);
                    }
                }
                *phy_reg = phy_reg_data;
                if (phy_reg_id == BCM2838_GENET_PHY_BMCR && anrestart == 1) {
                    bcm2838_genet_phy_update_link(s);
                }
            }
        }
    }

    return cmd;
}

/* Assemble a 64-bit value from its high and low 32-bit register halves */
static uint64_t bcm2838_genet_make_u64(uint32_t hi, uint32_t lo)
{
    return ((uint64_t)hi << 32) | lo;
}

static void bcm2838_genet_xmit_packet(BCM2838GenetNetState *dev, void *packet,
                                      size_t size)
{
    size_t status_size = (dev->regs.tbuf.ctrl & 1) ? sizeof(BCM2838GenetXmitStatus) : 0;
    uint8_t *buf = packet + status_size;
    size_t len;
    uint16_t len_type;

    /* We need the status block and a whole Ethernet header (len/type at 12) */
    if (size < status_size + ETH_HLEN) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: TX frame too short (%zu bytes)\n", __func__, size);
        return;
    }

    len = size - status_size;
    net_checksum_calculate(buf, len, CSUM_ALL);

    len_type = lduw_be_p(buf + 12);
    if (len_type < MAX_PAYLOAD_SIZE) {
        stw_be_p(buf + 12, len);
    }

    qemu_send_packet(qemu_get_queue(dev->nic), buf, len);
}

static uint64_t bcm2838_genet_tx(BCM2838GenetNetState *s, unsigned int ring_index,
                                 uint32_t prod_index,
                                 uint32_t cons_index)
{
    BCM2838GenetTdmaRing *ring = &s->regs.tdma.rings[ring_index];
    const unsigned int desc_size_words
        = sizeof(BCM2838GenetTdmaDesc) / sizeof(uint32_t);
    const uint64_t ring_start_addr
        = bcm2838_genet_make_u64(ring->start_addr_hi, ring->start_addr);
    const uint64_t ring_end_addr
        = bcm2838_genet_make_u64(ring->end_addr_hi, ring->end_addr);

    hwaddr data_addr;
    uint64_t desc_index;
    uint32_t desc_status = 0;
    uint32_t buflength = 0;
    uint64_t num_descs = 0;
    uint64_t read_ptr
        = bcm2838_genet_make_u64(ring->read_ptr_hi, ring->read_ptr);
    size_t packet_off = 0;

    uint32_t prod_index_fld = FIELD_EX32(prod_index,
                                         GENET_DMA_PROD_INDEX, INDEX);
    uint32_t cons_index_fld = FIELD_EX32(cons_index,
                                        GENET_DMA_CONS_INDEX, INDEX);

    /* Addresses are descriptor-RAM word offsets, not guest DMA pointers. */
    if (ring_start_addr % desc_size_words ||
        ring_end_addr >= BCM2838_GENET_DMA_DESC_CNT * desc_size_words ||
        ring_end_addr < ring_start_addr ||
        (ring_end_addr + 1) % desc_size_words ||
        read_ptr < ring_start_addr || read_ptr > ring_end_addr ||
        read_ptr % desc_size_words ||
        ((prod_index_fld - cons_index_fld) & 0xffff) >
            (ring_end_addr + 1 - ring_start_addr) / desc_size_words) {
        qemu_log_mask(LOG_GUEST_ERROR, "GENET invalid TX ring geometry/index distance\n");
        return 0;
    }
    while (cons_index_fld != prod_index_fld) {
        desc_index = read_ptr / desc_size_words;
        if (desc_index >= BCM2838_GENET_DMA_DESC_CNT) {
            qemu_log_mask(
                LOG_GUEST_ERROR,
                "%s: invalid TX descriptor index %" PRIu64 " (exceeds %u)\n",
                __func__, desc_index, BCM2838_GENET_DMA_DESC_CNT - 1);
            break;
        }
        desc_status = s->regs.tdma.descs[desc_index].length_status;
        data_addr = bcm2838_genet_make_u64(
            s->regs.tdma.descs[desc_index].address_hi,
            s->regs.tdma.descs[desc_index].address_lo);
        trace_bcm2838_genet_tx(ring_index, desc_index, desc_status,
                               data_addr);

        if (FIELD_EX32(desc_status, GENET_RDMA_LENGTH_STATUS, SOP) != 0) {
            packet_off = 0;
        }

        buflength = FIELD_EX32(desc_status,
                               GENET_RDMA_LENGTH_STATUS, BUFLENGTH);

        if (packet_off + buflength > sizeof(s->tx_packet)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "%s: TX frame too big (%zu + %u bytes, max %zu)\n",
                          __func__, packet_off, buflength,
                          sizeof(s->tx_packet));
            break;
        }

        if (address_space_read(&s->dma_as, data_addr,
                               MEMTXATTRS_UNSPECIFIED,
                               s->tx_packet + packet_off,
                               buflength) != MEMTX_OK) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "%s: failed to read TX buffer at 0x%" HWADDR_PRIx
                          "\n", __func__, data_addr);
            break;
        }
        packet_off += buflength;

        if (FIELD_EX32(desc_status, GENET_RDMA_LENGTH_STATUS, EOP) != 0) {
            bcm2838_genet_xmit_packet(s, s->tx_packet,
                                      packet_off);
            packet_off = 0;
        }

        num_descs++;
        cons_index_fld = (cons_index_fld + 1) & 0xffff;
        s->regs.tdma.descs[desc_index].length_status =
            FIELD_DP32(s->regs.tdma.descs[desc_index].length_status,
                       GENET_RDMA_LENGTH_STATUS, OWN, 1);
        read_ptr = read_ptr == ring_end_addr + 1 - desc_size_words
            ? ring_start_addr : read_ptr + desc_size_words;
    }

    ring->read_ptr = read_ptr;
    ring->read_ptr_hi = read_ptr >> 32;

    return num_descs;
}

static bool bcm2838_genet_tdma_ring_active(BCM2838GenetNetState *s,
                                           unsigned int ring_index)
{
    uint32_t ctrl_reg = s->regs.tdma.ctrl;
    uint32_t ring_cfg_reg = s->regs.tdma.ring_cfg;
    uint32_t ring_mask = 1 << ring_index;
    bool dma_en = FIELD_EX32(ctrl_reg, GENET_DMA_CTRL, EN);
    bool ring_en =
        FIELD_EX32(ring_cfg_reg, GENET_DMA_RING_CFG, EN) & ring_mask;
    bool ring_buf_en =
        FIELD_EX32(ctrl_reg, GENET_DMA_CTRL, RING_BUF_EN) & ring_mask;
    bool active = dma_en && ring_en && ring_buf_en &&
        FIELD_EX32(s->regs.umac.cmd, GENET_UMAC_CMD, TX_EN);

    trace_bcm2838_genet_tx_dma_ring_active(ring_index,
                                           active ? "active" : "halted");
    return active;
}

/* The guest wrote a ring's producer index: send what it has queued */
static void bcm2838_genet_tdma_prod_index_write(BCM2838GenetNetState *s,
                                                unsigned int ring_index,
                                                uint32_t prod_index)
{
    BCM2838GenetTdmaRing *ring = &s->regs.tdma.rings[ring_index];
    uint32_t cons_index = ring->cons_index;
    uint32_t cons_index_fld = FIELD_EX32(cons_index,
                                         GENET_DMA_CONS_INDEX, INDEX);
    uint32_t prod_index_fld = FIELD_EX32(prod_index,
                                         GENET_DMA_PROD_INDEX, INDEX);
    uint64_t num_descs_tx;
    uint32_t tx_intrs;

    if (cons_index_fld == prod_index_fld) {
        return;
    }

    trace_bcm2838_genet_tx_request(ring_index, prod_index_fld,
                                   cons_index_fld);
    num_descs_tx = bcm2838_genet_tx(s, ring_index, prod_index, cons_index);
    if (num_descs_tx == 0) {
        return;
    }

    ring->cons_index = FIELD_DP32(ring->cons_index, GENET_DMA_CONS_INDEX,
                                  INDEX, cons_index + num_descs_tx);

    if (ring_index == BCM2838_GENET_DMA_RING_DEFAULT) {
        s->regs.intrl0.stat = FIELD_DP32(s->regs.intrl0.stat, GENET_INTRL_0,
                                         TXDMA_MBDONE, 1);
    } else {
        tx_intrs = FIELD_EX32(s->regs.intrl1.stat, GENET_INTRL_1, TX_INTRS);
        s->regs.intrl1.stat = FIELD_DP32(s->regs.intrl1.stat, GENET_INTRL_1,
                                         TX_INTRS, tx_intrs | 1 << ring_index);
    }
}

static bool bcm2838_genet_rdma_ring_active(BCM2838GenetNetState *s,
                                           unsigned int ring_index)
{
    uint32_t ring_mask = 1 << ring_index;

    bool dma_en = FIELD_EX32(s->regs.rdma.ctrl, GENET_DMA_CTRL, EN);
    bool ring_en = FIELD_EX32(s->regs.rdma.ring_cfg, GENET_DMA_RING_CFG, EN)
                   & ring_mask;
    bool ring_buf_en = FIELD_EX32(s->regs.rdma.ctrl,
                                  GENET_DMA_CTRL, RING_BUF_EN) & ring_mask;
    bool active = dma_en && ring_en && ring_buf_en;

    trace_bcm2838_genet_rx_dma_ring_active(ring_index,
                                           active ? "active" : "halted");

    return active;
}

static void bcm2838_genet_tdma(BCM2838GenetNetState *s, hwaddr offset,
                               uint64_t value)
{
    hwaddr ring_offset;
    unsigned int ring_index;
    uint32_t prod_index = value;
    uint32_t ring_cfg = value;
    uint32_t dma_ctrl = value;

    uint32_t exst_tdma_en =
        FIELD_EX32(s->regs.tdma.ctrl, GENET_DMA_CTRL, EN);
    uint32_t exst_ring_en =
        FIELD_EX32(s->regs.tdma.ring_cfg, GENET_DMA_RING_CFG, EN);
    uint32_t incm_tdma_en =
        FIELD_EX32(dma_ctrl, GENET_DMA_CTRL, EN);
    uint32_t incm_ring_en =
        FIELD_EX32(ring_cfg, GENET_DMA_RING_CFG, EN);
    uint32_t incm_ring_buf_en =
        FIELD_EX32(dma_ctrl, GENET_DMA_CTRL, RING_BUF_EN);

    switch (offset) {
    case BCM2838_GENET_TDMA_RINGS
        ... BCM2838_GENET_TDMA_RINGS + sizeof(s->regs.tdma.rings) - 1:
        ring_index = (offset - BCM2838_GENET_TDMA_RINGS)
            / sizeof(BCM2838GenetTdmaRing);
        if (bcm2838_genet_tdma_ring_active(s, ring_index)) {
            ring_offset = offset - BCM2838_GENET_TDMA_RINGS
                - ring_index * sizeof(BCM2838GenetTdmaRing);
            switch (ring_offset) {
            case BCM2838_GENET_TRING_PROD_INDEX:
                bcm2838_genet_tdma_prod_index_write(s, ring_index,
                                                    prod_index);
                break;
            default:
                break;
            }
        }
        break;
    case BCM2838_GENET_TDMA_RING_CFG:
        if (exst_ring_en != incm_ring_en) {
            trace_bcm2838_genet_tx_dma_ring(incm_ring_en);
        }
        break;
    case BCM2838_GENET_TDMA_CTRL:
        s->regs.tdma.status = (~dma_ctrl) & GENET_DMA_ENABLE_MASK;
        if (exst_tdma_en != incm_tdma_en) {
            trace_bcm2838_genet_tx_dma(incm_tdma_en == 1
                                       ? "enabled"
                                       : "disabled");
        }
        if (exst_ring_en != incm_ring_buf_en) {
            trace_bcm2838_genet_tx_dma_ring_buf(incm_ring_buf_en);
        }
        break;
    default:
        break;
    }
}

static uint64_t bcm2838_genet_read(void *opaque, hwaddr offset, unsigned size)
{
    uint64_t value = ~0;
    BCM2838GenetNetState *s = opaque;

    if (offset + size <= sizeof(s->regs)) {
        value = *bcm2838_genet_reg(s, offset);
    } else {
        qemu_log_mask(
            LOG_GUEST_ERROR,
            "%s: out-of-range access, %u bytes @ offset 0x%04" PRIx64 "\n",
            __func__, size, offset);
    }

    trace_bcm2838_genet_read(size, offset, value);
    return value;
}

static void bcm2838_genet_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size)
{
    BCM2838GenetNetState *s = opaque;
    MACAddr *mac = &s->nic_conf.macaddr;
    NetClientState *ncs = qemu_get_queue(s->nic);

    trace_bcm2838_genet_write(size, offset, value);

    if (offset + size > sizeof(s->regs)) {
        qemu_log_mask(
            LOG_GUEST_ERROR,
            "%s: out-of-range access, %u bytes @ offset 0x%04" PRIx64 "\n",
            __func__, size, offset);
        return;
    }

    switch (offset) {
    case BCM2838_GENET_INTRL0_SET:
        s->regs.intrl0.stat |= value;
        break;
    case BCM2838_GENET_INTRL0_CLEAR:
        s->regs.intrl0.stat &= ~value;
        break;
    case BCM2838_GENET_INTRL0_MASK_SET:
        s->regs.intrl0.mask_status |= value;
        break;
    case BCM2838_GENET_INTRL0_MASK_CLEAR:
        s->regs.intrl0.mask_status &= ~value;
        break;
    case BCM2838_GENET_INTRL1_SET:
        s->regs.intrl1.stat |= value;
        break;
    case BCM2838_GENET_INTRL1_CLEAR:
        s->regs.intrl1.stat &= ~value;
        break;
    case BCM2838_GENET_INTRL1_MASK_SET:
        s->regs.intrl1.mask_status |= value;
        break;
    case BCM2838_GENET_INTRL1_MASK_CLEAR:
        s->regs.intrl1.mask_status &= ~value;
        break;
    case BCM2838_GENET_UMAC_CMD:
        /* Complete SW reset as soon as it has been requested */
        if (FIELD_EX32(value, GENET_UMAC_CMD, SW_RESET) == 1) {
            device_cold_reset(DEVICE(s));
            value = FIELD_DP32(value, GENET_UMAC_CMD, SW_RESET, 0);
        }
        break;
    /*
     * TODO: before changing MAC address we'd better inform QEMU
     * network subsystem about freeing previously used one, but
     * qemu_macaddr_set_free function isn't accessible for us (marked
     * as static in net/net.c), see also https://lists.nongnu.org/
     * archive/html/qemu-devel/2022-07/msg02123.html
     */
    case BCM2838_GENET_UMAC_MAC0:
        mac->a[0] = FIELD_EX32(value, GENET_UMAC_MAC_0, ADDR_0);
        mac->a[1] = FIELD_EX32(value, GENET_UMAC_MAC_0, ADDR_1);
        mac->a[2] = FIELD_EX32(value, GENET_UMAC_MAC_0, ADDR_2);
        mac->a[3] = FIELD_EX32(value, GENET_UMAC_MAC_0, ADDR_3);
        qemu_format_nic_info_str(ncs, mac->a);
        trace_bcm2838_genet_mac_address(ncs->info_str);
        break;
    case BCM2838_GENET_UMAC_MAC1:
        mac->a[4] = FIELD_EX32(value, GENET_UMAC_MAC_1, ADDR_4);
        mac->a[5] = FIELD_EX32(value, GENET_UMAC_MAC_1, ADDR_5);
        qemu_format_nic_info_str(ncs, mac->a);
        trace_bcm2838_genet_mac_address(ncs->info_str);
        break;
    case BCM2838_GENET_RDMA_CTRL:
        s->regs.rdma.status = (~value) & GENET_DMA_ENABLE_MASK;
        break;
    case BCM2838_GENET_UMAC_MDIO_CMD:
        value = bcm2838_genet_mdio_cmd(s, value);
        s->regs.intrl0.stat = FIELD_DP32(s->regs.intrl0.stat,
                                         GENET_INTRL_0, MDIO_DONE, 1);
        break;
    case BCM2838_GENET_TDMA_REGS
        ... BCM2838_GENET_TDMA_REGS + sizeof(BCM2838GenetRegsTdma) - 1:
        bcm2838_genet_tdma(s, offset, value);
        break;
    default:
        break;
    }

    *bcm2838_genet_reg(s, offset) = value;
    if (offset == BCM2838_GENET_RDMA_CTRL &&
        FIELD_EX32(value, GENET_DMA_CTRL, EN)) {
        qemu_flush_queued_packets(ncs);
    }
    bcm2838_genet_set_irq_default(s);
    bcm2838_genet_set_irq_prio(s);
}

static const MemoryRegionOps bcm2838_genet_ops = {
    .read = bcm2838_genet_read,
    .write = bcm2838_genet_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .impl = {.min_access_size = 4, .max_access_size = 4},
    .valid = {.min_access_size = 4, .max_access_size = 4},
};

static int32_t bcm2838_genet_filter(BCM2838GenetNetState *s, const void *buf,
                                    size_t size)
{
    qemu_log_mask(LOG_UNIMP,
                  "Packet filtration with HFB isn't implemented yet");
    return -1;
}

static int32_t bcm2838_genet_filter2ring(BCM2838GenetNetState *s,
                                         uint32_t filter_idx)
{
    qemu_log_mask(LOG_UNIMP,
                  "Packet filtration with HFB isn't implemented yet");
    return -1;
}

static bool is_packet_broadcast(const uint8_t *buf, size_t size)
{
    static const uint8_t bcst_addr[] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};

    if (size < sizeof(bcst_addr)) {
        return false;
    }

    return !memcmp(buf, bcst_addr, sizeof(bcst_addr));
}

static bool is_packet_multicast(const uint8_t *buf, size_t size)
{
    return !!(buf[0] & 0x01);
}

/* Normal Ethernet frames fit one configured receive buffer. Jumbo/scatter RX
 * is deliberately rejected rather than overwriting another owned buffer. */
static ssize_t bcm2838_genet_rdma(BCM2838GenetNetState *s, uint32_t ring_idx,
                                  const void *buf, size_t size)
{
    BCM2838GenetRegsRdma *rdma = &s->regs.rdma;
    BCM2838GenetRdmaRing *ring = &rdma->rings[ring_idx];
    uint64_t start = bcm2838_genet_make_u64(ring->start_addr_hi, ring->start_addr);
    uint64_t end = bcm2838_genet_make_u64(ring->end_addr_hi, ring->end_addr);
    uint64_t ptr = bcm2838_genet_make_u64(ring->write_ptr_hi, ring->write_ptr);
    unsigned count = ring->ring_buf_size >> 16;
    unsigned capacity = ring->ring_buf_size & 0xffff;
    uint16_t prod = ring->prod_index, cons = ring->cons_index;
    bool status64 = s->regs.rbuf.ctrl & 1;
    size_t status_size = status64 ? sizeof(BCM2838GenetXmitStatus) : 0;
    size_t align_size = (s->regs.rbuf.ctrl & 2) ? 2 : 0;
    bool crc = FIELD_EX32(s->regs.umac.cmd, GENET_UMAC_CMD, CRC_FWD);
    size_t total = status_size + align_size + size + (crc ? 4 : 0);
    BCM2838GenetRdmaDesc *desc;
    uint32_t status;
    hwaddr addr;

    if (!bcm2838_genet_rdma_ring_active(s, ring_idx) ||
        !FIELD_EX32(s->regs.umac.cmd, GENET_UMAC_CMD, RX_EN)) {
        return size;
    }
    if (start % 3 || end >= BCM2838_GENET_DMA_DESC_CNT * 3 ||
        end < start || (end + 1) % 3 || ptr < start || ptr > end || ptr % 3 ||
        !count || count > (end + 1 - start) / 3 ||
        (uint16_t)(prod - cons) >= count ||
        total > capacity || total > sizeof(s->rx_packet)) {
        ring->prod_index += 1U << 16; /* hardware discarded-frame counter */
        return size;
    }
    desc = &rdma->descs[ptr / 3];
    status = (total << 16) | (1U << 13) | (1U << 14);
    status |= is_packet_broadcast(buf, size) ? 1U << 6 : 0;
    status |= is_packet_multicast(buf, size) ? 1U << 5 : 0;
    memset(s->rx_packet, 0, total);
    memcpy(s->rx_packet + status_size + align_size, buf, size);
    if (crc) {
        stl_le_p(s->rx_packet + total - 4, ~net_crc32_le(buf, size));
    }
    if (status64) {
        stl_le_p(s->rx_packet, status);
    }
    addr = bcm2838_genet_make_u64(desc->address_hi, desc->address_lo);
    if (address_space_write(&s->dma_as, addr, MEMTXATTRS_UNSPECIFIED,
                            s->rx_packet, total) != MEMTX_OK) {
        desc->length_status = status | (1U << 2);
        return size; /* no successful completion/IRQ on failed DMA */
    }
    desc->length_status = status;
    ring->prod_index = (ring->prod_index & 0xffff0000) | (uint16_t)(prod + 1);
    ptr = ptr + 3 > end ? start : ptr + 3;
    ring->write_ptr = ptr;
    ring->write_ptr_hi = ptr >> 32;
    if (ring_idx == BCM2838_GENET_DMA_RING_DEFAULT) {
        s->regs.intrl0.stat |= 1U << 13;
    } else {
        s->regs.intrl1.stat |= 1U << (16 + ring_idx);
    }
    return size;
}

static ssize_t bcm2838_genet_receive(NetClientState *nc, const uint8_t *buf,
                                     size_t size)
{
    BCM2838GenetNetState *s = (BCM2838GenetNetState *)qemu_get_nic_opaque(nc);
    ssize_t bytes_received = -1;
    int32_t filter_index = -1;
    int32_t ring_index = -1;

    if (size < ETH_HLEN || size > MAX_PACKET_SIZE) {
        return size;
    }
    if (FIELD_EX32(s->regs.rdma.ctrl, GENET_DMA_CTRL, EN) != 0) {
        filter_index = bcm2838_genet_filter(s, buf, size);

        if (filter_index >= 0) {
            ring_index = bcm2838_genet_filter2ring(s, filter_index);
        } else {
            ring_index = BCM2838_GENET_DMA_RING_CNT - 1;
            if (!bcm2838_genet_rdma_ring_active(s, ring_index)) {
                for (ring_index = 0;
                     ring_index < BCM2838_GENET_DMA_RING_CNT - 1;
                     ring_index++) {
                    if (bcm2838_genet_rdma_ring_active(s, ring_index)) {
                        break;
                    }
                }
            }
        }

        if (size <= MAX_PACKET_SIZE) {
            bytes_received = bcm2838_genet_rdma(s, ring_index, buf, size);
        }
    }

    bcm2838_genet_set_irq_default(s);
    bcm2838_genet_set_irq_prio(s);

    return bytes_received;
}

static void bcm2838_genet_phy_update_link(BCM2838GenetNetState *s)
{
    bool qemu_link_down = !qemu_get_queue(s->nic)->peer ||
                          qemu_get_queue(s->nic)->link_down;

    bool lstatus = FIELD_EX32(s->phy_regs.bmsr, GENET_PHY_BMSR, LSTATUS);

    if (qemu_link_down && lstatus) {
        trace_bcm2838_genet_phy_update_link("down");

        s->phy_regs.bmsr = FIELD_DP32(s->phy_regs.bmsr,
                                      GENET_PHY_BMSR, ANEGCOMPLETE, 0);
        s->phy_regs.bmsr = FIELD_DP32(s->phy_regs.bmsr,
                                      GENET_PHY_BMSR, LSTATUS, 0);
        s->regs.intrl0.stat = FIELD_DP32(s->regs.intrl0.stat,
                                         GENET_INTRL_0, LINK_DOWN, 1);
    } else if (!qemu_link_down && !lstatus) {
        trace_bcm2838_genet_phy_update_link("up");

        /*
         * Complete auto-negotiation (fixed link partner's abilities for now:
         * 1Gbps with flow control)
         */
        s->phy_regs.stat1000 = FIELD_DP32(s->phy_regs.stat1000,
                                          GENET_PHY_STAT_1000, HALF, 1);
        s->phy_regs.stat1000 = FIELD_DP32(s->phy_regs.stat1000,
                                          GENET_PHY_STAT_1000, FULL, 1);

        s->phy_regs.lpa = FIELD_DP32(s->phy_regs.lpa,
                                     GENET_PHY_LPA, PAUSE_CAP, 1);
        s->phy_regs.lpa = FIELD_DP32(s->phy_regs.lpa,
                                     GENET_PHY_LPA, PAUSE_ASYM, 1);
        s->phy_regs.lpa = FIELD_DP32(s->phy_regs.lpa, GENET_PHY_LPA, LPACK, 1);

        s->phy_regs.bmsr = FIELD_DP32(s->phy_regs.bmsr,
                                      GENET_PHY_BMSR, ANEGCOMPLETE, 1);
        s->phy_regs.bmsr = FIELD_DP32(s->phy_regs.bmsr,
                                      GENET_PHY_BMSR, LSTATUS, 1);

        s->regs.intrl0.stat = FIELD_DP32(s->regs.intrl0.stat,
                                         GENET_INTRL_0, LINK_UP, 1);
    }

    /* BCM54xx AUXSTS (MDIO0x19): brgphy consumes the negotiated result here.
     * Our fixed partner is 1000BASE-T full duplex; derive carrier from backend,
     * never from guest writes to a status register. */
    s->phy_regs.aux_status = qemu_link_down ? 0 : 0x8704;
    bcm2838_genet_set_irq_default(s);
}
static void bcm2838_genet_set_link(NetClientState *nc)
{
    BCM2838GenetNetState *s = qemu_get_nic_opaque(nc);

    bcm2838_genet_phy_update_link(s);
}

static NetClientInfo bcm2838_genet_client_info = {
    .type = NET_CLIENT_DRIVER_NIC,
    .size = sizeof(NICState),
    .receive = bcm2838_genet_receive,
    .link_status_changed = bcm2838_genet_set_link,
};

static void bcm2838_genet_realize(DeviceState *dev, Error **errp)
{
    NetClientState *ncs;
    BCM2838GenetNetState *s = BCM2838_GENET_NET(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);

    if (s->phy_addr > 31) {
        error_setg(errp, "GENET phy-addr must be in0..31");
        return;
    }
    if (!s->ram) {
        error_setg(errp, "GENET requires a RAM-only DMA address space");
        return;
    }

    /* Controller registers */
    memory_region_init_io(&s->regs_mr, OBJECT(s), &bcm2838_genet_ops, s,
                          "bcm2838_genet_regs", sizeof(s->regs));
    sysbus_init_mmio(sbd, &s->regs_mr);

    /* QEMU-managed NIC (host network back-end connection) */
    qemu_macaddr_default_if_unset(&s->nic_conf.macaddr);
    s->nic = qemu_new_nic(&bcm2838_genet_client_info, &s->nic_conf,
                          object_get_typename(OBJECT(dev)), dev->id,
                          &dev->mem_reentrancy_guard, s);
    bcm2838_genet_set_qemu_mac(s);
    ncs = qemu_get_queue(s->nic);
    qemu_format_nic_info_str(ncs, s->nic_conf.macaddr.a);
    trace_bcm2838_genet_mac_address(ncs->info_str);

    /* Interrupts */
    sysbus_init_irq(sbd, &s->irq_default);
    sysbus_init_irq(sbd, &s->irq_prio);

    /* DMA space */
    address_space_init(&s->dma_as, s->ram, "bcm2838_genet_dma");
}

static void bcm2838_genet_phy_reset(BCM2838GenetNetState *s)
{
    memset(&s->phy_regs, 0x00, sizeof(s->phy_regs));
    memset(&s->phy_shd_regs, 0x00, sizeof(s->phy_shd_regs));
    memset(&s->phy_aux_ctl_shd_regs, 0x00, sizeof(s->phy_aux_ctl_shd_regs));

    /* All values below were taken from real HW trace and logs */
    s->phy_regs.bmcr = 0x1140;
    s->phy_regs.bmsr = 0x7949;
    s->phy_regs.sid1 = 0x600D;
    s->phy_regs.sid2 = 0x84A2;
    s->phy_regs.advertise = 0x01E1;
    s->phy_regs.ctrl1000 = 0x0200;
    s->phy_regs.estatus = 0x3000;

    s->phy_shd_regs.clk_ctl = 0x0200;
    s->phy_shd_regs.scr3 = 0x001F;
    s->phy_shd_regs.apd = 0x0001;

    s->phy_aux_ctl_shd_regs.regs[BCM2838_GENET_PHY_AUX_CTL_MISC] = 0x1E;

    trace_bcm2838_genet_phy_reset("done");

    bcm2838_genet_phy_update_link(s);
}

static void bcm2838_genet_reset(Object *obj, ResetType type)
{
    BCM2838GenetNetState *s = BCM2838_GENET_NET(obj);

    memset(&s->regs, 0x00, sizeof(s->regs));

    s->regs.sys.rev_ctrl = FIELD_DP32(s->regs.sys.rev_ctrl, GENET_SYS_REV_CTRL,
                                      MAJOR_REV, BCM2838_GENET_REV_MAJOR);
    s->regs.sys.rev_ctrl = FIELD_DP32(s->regs.sys.rev_ctrl, GENET_SYS_REV_CTRL,
                                      MINOR_REV, BCM2838_GENET_REV_MINOR);
    s->regs.intrl0.mask_status = UINT32_MAX;
    s->regs.intrl1.mask_status = UINT32_MAX;
    s->regs.rdma.status = GENET_DMA_ENABLE_MASK;
    s->regs.tdma.status = GENET_DMA_ENABLE_MASK;

    trace_bcm2838_genet_reset("done");

    bcm2838_genet_set_qemu_mac(s);
    bcm2838_genet_phy_reset(s);
    bcm2838_genet_set_irq_default(s);
    bcm2838_genet_set_irq_prio(s);
}

static const Property genet_properties[] = {
    DEFINE_NIC_PROPERTIES(BCM2838GenetNetState, nic_conf),
    DEFINE_PROP_UINT8("phy-addr", BCM2838GenetNetState, phy_addr, 1),
    DEFINE_PROP_LINK("ram", BCM2838GenetNetState, ram, TYPE_MEMORY_REGION, MemoryRegion *),
};

static int bcm2838_genet_post_load(void *opaque, int version_id)
{
    BCM2838GenetNetState *s = opaque;
    bcm2838_genet_set_irq_default(s);
    bcm2838_genet_set_irq_prio(s);
    return 0;
}

static const VMStateDescription vmstate_bcm2838_genet = {
    .name = "bcm2838-genet",
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2838_genet_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT8_EQUAL(phy_addr, BCM2838GenetNetState, "GENET PHY strap differs"),
        VMSTATE_UINT32_ARRAY(regs_u32, BCM2838GenetNetState,
                             sizeof(BCM2838GenetRegs) / sizeof(uint32_t)),
        VMSTATE_UINT16_ARRAY(phy_regs_u16, BCM2838GenetNetState,
                             sizeof(BCM2838GenetPhyRegs) / sizeof(uint16_t)),
        VMSTATE_UINT16_ARRAY(phy_shd_regs_u16, BCM2838GenetNetState,
                             sizeof(BCM2838GenetPhyShdRegs)
                             / sizeof(uint16_t)),
        VMSTATE_UINT16_ARRAY(phy_aux_ctl_shd_regs.regs, BCM2838GenetNetState,
                             BCM2838_GENET_PHY_AUX_CTL_REGS_SIZE),
        VMSTATE_UINT16_2DARRAY(phy_exp_shd_regs.regs, BCM2838GenetNetState,
                               BCM2838_GENET_PHY_EXP_SHD_BLOCKS_CNT,
                               BCM2838_GENET_PHY_EXP_SHD_REGS_CNT),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2838_genet_class_init(ObjectClass *class, const void *data)
{
    static ResettablePhases unused_parent_phases;
    DeviceClass *dc = DEVICE_CLASS(class);
    ResettableClass *rc = RESETTABLE_CLASS(class);

    dc->realize = bcm2838_genet_realize;
    dc->vmsd = &vmstate_bcm2838_genet;
    resettable_class_set_parent_phases(rc, NULL, bcm2838_genet_reset, NULL,
                                       &unused_parent_phases);
    device_class_set_props(dc, genet_properties);
}

static const TypeInfo bcm2838_genet_info = {
    .name       = TYPE_BCM2838_GENET_NET,
    .parent     = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2838GenetNetState),
    .class_init = bcm2838_genet_class_init,
};

static void bcm2838_genet_register(void)
{
    type_register_static(&bcm2838_genet_info);
}

type_init(bcm2838_genet_register)
