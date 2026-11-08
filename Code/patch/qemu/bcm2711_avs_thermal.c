/* SPDX-License-Identifier: GPL-2.0-or-later */
/* BCM2711 AVS ring-oscillator temperature status register.
 * This is an injected, quantized sensor reading, not a thermal physics model.
 * Calibration follows the BCM2711 FDT/EDK2 contract: mC = 410040 - 487 * raw.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/misc/bcm2711_avs_thermal.h"
#include "migration/vmstate.h"

#define TEMP_MIN_MC (-40000)
#define TEMP_MAX_MC 125000
#define TEMP_DEFAULT_MC 42000
#define TEMP_VALID (1u << 10)

static MemTxResult avs_read(void *opaque, hwaddr offset, uint64_t *data,
                           unsigned size, MemTxAttrs attrs)
{
    BCM2711AvsThermalState *s = opaque;
    uint32_t raw;

    *data = 0;
    if (offset != 0 || size != 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2711-avs-thermal: unsupported %u-byte read at 0x%"
                      HWADDR_PRIx "\n", size, offset);
        return MEMTX_ERROR;
    }
    /* All bounded inputs produce a 10-bit code; round to nearest HW step. */
    raw = (410040 - s->temperature_millicelsius + 487 / 2) / 487;
    *data = TEMP_VALID | raw;
    return MEMTX_OK;
}

static MemTxResult avs_write(void *opaque, hwaddr offset, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    /* The only implemented register is read-only. Valid word writes do not
     * change the injected reading; other accesses have no modeled register.
     */
    qemu_log_mask(LOG_GUEST_ERROR,
                  "bcm2711-avs-thermal: ignored %u-byte write at 0x%"
                  HWADDR_PRIx "\n", size, offset);
    return offset == 0 && size == 4 ? MEMTX_OK : MEMTX_ERROR;
}

static const MemoryRegionOps avs_ops = {
    .read_with_attrs = avs_read,
    .write_with_attrs = avs_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Deliver widths intact to the handler so unsupported accesses fail
     * instead of being split or widened into valid status reads.
     */
    .valid.min_access_size = 1, .valid.max_access_size = 8,
    .valid.unaligned = true,
    .impl.min_access_size = 1, .impl.max_access_size = 8,
    .impl.unaligned = true,
};

static void avs_get_temperature(Object *obj, Visitor *v, const char *name,
                                void *opaque, Error **errp)
{
    int64_t value = BCM2711_AVS_THERMAL(obj)->temperature_millicelsius;

    visit_type_int(v, name, &value, errp);
}

static void avs_set_temperature(Object *obj, Visitor *v, const char *name,
                                void *opaque, Error **errp)
{
    int64_t value;

    if (!visit_type_int(v, name, &value, errp)) {
        return;
    }
    if (value < TEMP_MIN_MC || value > TEMP_MAX_MC) {
        error_setg(errp, "BCM2711 temperature must be between -40000 and 125000 millicelsius");
        return;
    }
    BCM2711_AVS_THERMAL(obj)->temperature_millicelsius = value;
}

static void avs_reset(DeviceState *dev)
{
    BCM2711_AVS_THERMAL(dev)->temperature_millicelsius = TEMP_DEFAULT_MC;
}

static int avs_post_load(void *opaque, int version)
{
    BCM2711AvsThermalState *s = opaque;

    return s->temperature_millicelsius >= TEMP_MIN_MC &&
           s->temperature_millicelsius <= TEMP_MAX_MC ? 0 : -EINVAL;
}

static const VMStateDescription vmstate_avs = {
    .name = TYPE_BCM2711_AVS_THERMAL,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = avs_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_INT32(temperature_millicelsius, BCM2711AvsThermalState),
        VMSTATE_END_OF_LIST()
    }
};

static void avs_init(Object *obj)
{
    BCM2711AvsThermalState *s = BCM2711_AVS_THERMAL(obj);

    s->temperature_millicelsius = TEMP_DEFAULT_MC;
    memory_region_init_io(&s->iomem, obj, &avs_ops, s,
                          TYPE_BCM2711_AVS_THERMAL, 8);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    object_property_add(obj, "temperature-millicelsius", "int",
                        avs_get_temperature, avs_set_temperature, NULL, NULL);
}

static void avs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, avs_reset);
    dc->vmsd = &vmstate_avs;
}

static const TypeInfo avs_info = {
    .name = TYPE_BCM2711_AVS_THERMAL,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2711AvsThermalState),
    .instance_init = avs_init,
    .class_init = avs_class_init,
};

static void avs_register(void)
{
    type_register_static(&avs_info);
}
type_init(avs_register)
