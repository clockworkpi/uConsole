/*
 * Raspberry Pi 5 / BCM2712 SD bootstrap machine.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Model scope: Cortex-A76/PSCI, GIC-400, debug PL011, SDHCI and
 * boot-time pin configuration. VideoCore boot ROM and RP1 are not emulated.
 * Address/interrupt references: enrolled BCM2712 D0 firmware FDT and
 * raspberrypi/linux bcm2712.dtsi. No BCM2711 machine is inherited.
 */
#include "qemu/osdep.h"
#include "qemu/units.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "hw/boards.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/intc/arm_gic.h"
#include "hw/char/pl011.h"
#include "hw/sd/sdhci.h"
#include "hw/sd/sd.h"
#include "hw/misc/bcm2838_rng.h"
#include "hw/misc/bcm2712_boot.h"
#include "hw/qdev-properties.h"
#include "hw/qdev-properties-system.h"
#include "system/address-spaces.h"
#include "system/blockdev.h"
#include "system/system.h"
#include "system/device_tree.h"
#include "qemu/module.h"
#include "target/arm/cpu.h"

#define TYPE_RASPI5_MACHINE MACHINE_TYPE_NAME("raspi5b")
OBJECT_DECLARE_SIMPLE_TYPE(Raspi5MachineState, RASPI5_MACHINE)
#define PI5_CPUS 4
#define PI5_SPIS 320
#define PI5_PPI(cpu, id) (PI5_SPIS + (cpu) * 32 + (id))

struct Raspi5MachineState {
    MachineState parent_obj;
    ARMCPU *cpu[PI5_CPUS];
    DeviceState *gic;
    struct arm_boot_info boot;

};

static void raspi5_modify_dtb(const struct arm_boot_info *info, void *fdt)
{
    MachineState *ms = MACHINE(qdev_get_machine());
    uint32_t banks[12];
    unsigned count = 0;
    /* The firmware root uses 2 address cells and 1 size cell. Encode all
     * banks as tuples in ONE memory node: the FreeBSD Linux boot ABI's
     * fdt_foreach_mem_region reads that node's complete reg property. */
    for (uint64_t base = 0; base < ms->ram_size; base += 2 * GiB) {
        banks[count++] = cpu_to_be32(base >> 32);
        banks[count++] = cpu_to_be32(base);
        banks[count++] = cpu_to_be32(2 * GiB);
    }
    qemu_fdt_setprop(fdt, "/memory@0", "reg", banks, count * sizeof(*banks));
}

static void raspi5_init(MachineState *ms)
{
    Raspi5MachineState *s = RASPI5_MACHINE(ms);
    MemoryRegion *sysmem = get_system_memory();
    DeviceState *uart, *sd, *rng, *regs;
    DriveInfo *di;
    DeviceState *card;
    static const int timer_outputs[] = {GTIMER_PHYS, GTIMER_VIRT,
                                       GTIMER_HYP, GTIMER_SEC};
    static const int timer_ppis[] = {30, 27, 26, 29};

    if (ms->ram_size != 2 * GiB && ms->ram_size != 4 * GiB &&
        ms->ram_size != 8 * GiB) {
        error_report("raspi5b supports 2, 4 or 8 GiB RAM");
        exit(1);
    }
    memory_region_add_subregion(sysmem, 0, ms->ram);
    for (int i = 0; i < PI5_CPUS; i++) {
        s->cpu[i] = ARM_CPU(object_new(ms->cpu_type));
        object_property_add_child(OBJECT(ms), "cpu[*]", OBJECT(s->cpu[i]));
        object_property_set_int(OBJECT(s->cpu[i]), "mp-affinity", i << 8,
                                &error_fatal);
        object_property_set_bool(OBJECT(s->cpu[i]), "start-powered-off", i != 0,
                                 &error_fatal);
        object_property_set_int(OBJECT(s->cpu[i]), "psci-conduit",
                                QEMU_PSCI_CONDUIT_SMC, &error_fatal);
        qdev_realize(DEVICE(s->cpu[i]), NULL, &error_fatal);
    }
    s->gic = qdev_new(TYPE_ARM_GIC);
    object_property_add_child(OBJECT(ms), "gic", OBJECT(s->gic));
    qdev_prop_set_uint32(s->gic, "revision", 2);
    qdev_prop_set_uint32(s->gic, "num-cpu", PI5_CPUS);
    qdev_prop_set_uint32(s->gic, "num-irq", PI5_SPIS + 32);
    qdev_prop_set_bit(s->gic, "has-virtualization-extensions", true);
    sysbus_realize(SYS_BUS_DEVICE(s->gic), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->gic), 0, 0x107fff9000ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->gic), 1, 0x107fffa000ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->gic), 2, 0x107fffc000ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->gic), 3, 0x107fffe000ULL);
    for (int i = 0; i < PI5_CPUS; i++) {
        DeviceState *cpu = DEVICE(s->cpu[i]);
        sysbus_connect_irq(SYS_BUS_DEVICE(s->gic), i,
                           qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(s->gic), i + PI5_CPUS,
                           qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(s->gic), i + PI5_CPUS * 2,
                           qdev_get_gpio_in(cpu, ARM_CPU_VIRQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(s->gic), i + PI5_CPUS * 3,
                           qdev_get_gpio_in(cpu, ARM_CPU_VFIQ));
        sysbus_connect_irq(SYS_BUS_DEVICE(s->gic), i + PI5_CPUS * 4,
                           qdev_get_gpio_in(s->gic, PI5_PPI(i, 25)));
        for (int t = 0; t < ARRAY_SIZE(timer_outputs); t++) {
            qdev_connect_gpio_out(cpu, timer_outputs[t],
                qdev_get_gpio_in(s->gic, PI5_PPI(i, timer_ppis[t])));
        }
    }
    uart = qdev_new(TYPE_PL011);
    qdev_prop_set_chr(uart, "chardev", serial_hd(0));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(uart), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(uart), 0, 0x107d001000ULL);
    sysbus_connect_irq(SYS_BUS_DEVICE(uart), 0, qdev_get_gpio_in(s->gic, 120));

    rng = qdev_new(TYPE_BCM2838_RNG);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(rng), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(rng), 0, 0x107d208000ULL);

    sd = qdev_new(TYPE_SYSBUS_SDHCI);
    object_property_add_child(OBJECT(ms), "sdhci", OBJECT(sd));
    qdev_prop_set_uint8(sd, "sd-spec-version", 3);
    qdev_prop_set_uint64(sd, "capareg", 0x052134b4);
    sysbus_realize(SYS_BUS_DEVICE(sd), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(sd), 0, 0x1000fff000ULL);
    sysbus_connect_irq(SYS_BUS_DEVICE(sd), 0, qdev_get_gpio_in(s->gic, 273));
    di = drive_get(IF_SD, 0, 0);
    if (di) {
        card = qdev_new(TYPE_SD_CARD);
        qdev_prop_set_drive_err(card, "drive", blk_by_legacy_dinfo(di), &error_fatal);
        qdev_realize_and_unref(card, qdev_get_child_bus(sd, "sd-bus"), &error_fatal);
    }
    regs = qdev_new(TYPE_BCM2712_BOOT);
    object_property_set_link(OBJECT(regs), "sdhci", OBJECT(sd), &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(regs), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(regs), 0, 0x1000fff400ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(regs), 1, 0x107d504100ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(regs), 2, 0x107d510700ULL);
    sysbus_mmio_map(SYS_BUS_DEVICE(regs), 3, 0x107d517c00ULL);

    s->boot.loader_start = 0;
    s->boot.ram_size = 2 * GiB;
    s->boot.modify_dtb = raspi5_modify_dtb;
    s->boot.psci_conduit = QEMU_PSCI_CONDUIT_SMC;
    arm_load_kernel(s->cpu[0], ms, &s->boot);
}

static void raspi5_class_init(ObjectClass *oc, const void *data)
{
    static const char * const valid_cpu_types[] = {
        ARM_CPU_TYPE_NAME("cortex-a76"), NULL
    };
    MachineClass *mc = MACHINE_CLASS(oc);
    mc->valid_cpu_types = valid_cpu_types;
    mc->desc = "Raspberry Pi 5 BCM2712 SD bootstrap (RP1 not implemented)";
    mc->init = raspi5_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a76");
    mc->default_cpus = mc->min_cpus = mc->max_cpus = PI5_CPUS;
    mc->default_ram_size = 8 * GiB;
    mc->default_ram_id = "pi5.ram";
    mc->no_parallel = 1;
}
static const TypeInfo raspi5_type = {
    .name = TYPE_RASPI5_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(Raspi5MachineState),
    .class_init = raspi5_class_init,
    .interfaces = aarch64_machine_interfaces,
};
static void raspi5_register_types(void) { type_register_static(&raspi5_type); }
type_init(raspi5_register_types)
