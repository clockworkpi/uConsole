/* SPDX-License-Identifier: GPL-2.0-or-later */
/* TI ADC101C: virtual-clock digital model, not an electrical simulation. */
#include "qemu/osdep.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "hw/i2c/i2c.h"
#include "hw/irq.h"
#include "hw/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "adc101c-core.h"

#define TYPE_ADC101C "adc101c"
OBJECT_DECLARE_SIMPLE_TYPE(ADC101CState, ADC101C)

struct ADC101CState {
    I2CSlave parent_obj;
    Adc101cCore core;
    bool reference_supply_present;
    bool saved_reference_supply_present;
    QEMUTimer *timer;
    qemu_irq alert[2]; /* level and drive-enable; no invented board IRQ wire */
};

static void adc101c_update(ADC101CState *s)
{
    adc101c_advance(&s->core, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    if (s->core.deadline == ADC_NEVER) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, s->core.deadline);
    }
    qemu_set_irq(s->alert[0], adc101c_alert_level(&s->core));
    qemu_set_irq(s->alert[1], adc101c_alert_enabled(&s->core));
}

static void adc101c_tick(void *opaque)
{
    adc101c_update(opaque);
}

static int adc101c_event(I2CSlave *i2c, enum i2c_event event)
{
    ADC101CState *s = ADC101C(i2c);
    adc101c_update(s);
    if (event == I2C_START_SEND || event == I2C_START_RECV) {
        return adc101c_begin(&s->core, event == I2C_START_SEND) ? 0 : -1;
    }
    return 0;
}

static uint8_t adc101c_rx(I2CSlave *i2c)
{
    ADC101CState *s = ADC101C(i2c);
    uint8_t value;
    adc101c_update(s);
    value = adc101c_recv(&s->core);
    adc101c_update(s);
    return value;
}

static int adc101c_tx(I2CSlave *i2c, uint8_t byte)
{
    ADC101CState *s = ADC101C(i2c);
    bool accepted;
    adc101c_update(s);
    accepted = adc101c_send(&s->core, byte);
    adc101c_update(s);
    return accepted ? 0 : -1;
}

static void adc101c_get(Object *obj, Visitor *v, const char *name,
                        void *opaque, Error **errp)
{
    ADC101CState *s = ADC101C(obj);
    int64_t value;
    adc101c_update(s);
    if (!strcmp(name, "input-uv")) {
        value = s->core.input_uv;
    } else if (!strcmp(name, "reference-uv")) {
        value = s->core.reference_uv;
    } else {
        value = adc101c_value(&s->core, 0);
    }
    visit_type_int(v, name, &value, errp);
}

static void adc101c_set_input(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    ADC101CState *s = ADC101C(obj);
    int64_t value;
    if (!visit_type_int(v, name, &value, errp)) {
        return;
    }
    if (value < 0 || value > s->core.reference_uv) {
        error_setg(errp, "ADC input must be between 0 and 3300000 microvolts");
        return;
    }
    adc101c_update(s);
    adc101c_input(&s->core, value, s->core.reference_uv);
}

static bool adc101c_get_power(Object *obj, Error **errp)
{
    return ADC101C(obj)->core.powered;
}

static void adc101c_set_power(Object *obj, bool value, Error **errp)
{
    ADC101CState *s = ADC101C(obj);
    adc101c_update(s);
    adc101c_power(&s->core, value);
    adc101c_update(s);
}

static bool adc101c_get_alert(Object *obj, Error **errp)
{
    ADC101CState *s = ADC101C(obj);
    adc101c_update(s);
    return adc101c_alert_level(&s->core);
}

static bool adc101c_get_drive(Object *obj, Error **errp)
{
    return adc101c_alert_enabled(&ADC101C(obj)->core);
}

static void adc101c_reset_enter(Object *obj, ResetType type)
{
    ADC101CState *s = ADC101C(obj);
    adc101c_reset(&s->core);
    s->core.now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    adc101c_update(s);
}

static void adc101c_init_obj(Object *obj)
{
    ADC101CState *s = ADC101C(obj);
    adc101c_init(&s->core);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, adc101c_tick, s);
    qdev_init_gpio_out_named(DEVICE(obj), s->alert, "alert", 2);
    object_property_add(obj, "input-uv", "int", adc101c_get,
                        adc101c_set_input, NULL, NULL);
    object_property_add(obj, "reference-uv", "int", adc101c_get, NULL, NULL, NULL);
    object_property_add(obj, "conversion-word", "int", adc101c_get, NULL, NULL, NULL);
    object_property_add_bool(obj, "powered", adc101c_get_power, adc101c_set_power);
    object_property_add_bool(obj, "alert-level", adc101c_get_alert, NULL);
    object_property_add_bool(obj, "alert-enabled", adc101c_get_drive, NULL);
}

static void adc101c_finalize(Object *obj)
{
    timer_free(ADC101C(obj)->timer);
}

static int adc101c_pre_save(void *opaque)
{
    ADC101CState *s = opaque;
    s->saved_reference_supply_present = s->reference_supply_present;
    adc101c_update(s);
    return adc101c_valid_state(&s->core) ? 0 : -EINVAL;
}

static int adc101c_pre_load(void *opaque)
{
    /* Version 1 had only the fixed-reference device-tree profile. */
    ADC101C(opaque)->saved_reference_supply_present = true;
    return 0;
}

static int adc101c_post_load(void *opaque, int version_id)
{
    ADC101CState *s = opaque;
    if (!adc101c_valid_state(&s->core) || s->core.reference_uv != 3300000 ||
        s->saved_reference_supply_present != s->reference_supply_present) {
        return -EINVAL;
    }
    /* Preserve the virtual-clock deadline and partial I2C latches. Do not
     * advance a conversion or synthesize an I2C START during restoration. */
    timer_del(s->timer);
    if (s->core.deadline != ADC_NEVER) {
        timer_mod_ns(s->timer, s->core.deadline);
    }
    qemu_set_irq(s->alert[0], adc101c_alert_level(&s->core));
    qemu_set_irq(s->alert[1], adc101c_alert_enabled(&s->core));
    return 0;
}

static const VMStateDescription adc101c_vmstate = {
    .name = TYPE_ADC101C,
    .version_id = 2,
    .minimum_version_id = 1,
    .pre_save = adc101c_pre_save,
    .pre_load = adc101c_pre_load,
    .post_load = adc101c_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_I2C_SLAVE(parent_obj, ADC101CState),
        VMSTATE_UINT16_ARRAY(core.reg, ADC101CState, 8),
        VMSTATE_UINT16(core.sampled, ADC101CState),
        VMSTATE_UINT16(core.read_latch, ADC101CState),
        VMSTATE_UINT16(core.write_latch, ADC101CState),
        VMSTATE_UINT32(core.input_uv, ADC101CState),
        VMSTATE_UINT32(core.reference_uv, ADC101CState),
        VMSTATE_UINT64(core.now, ADC101CState),
        VMSTATE_UINT64(core.deadline, ADC101CState),
        VMSTATE_UINT64(core.started, ADC101CState),
        VMSTATE_UINT8(core.pointer, ADC101CState),
        VMSTATE_UINT8(core.position, ADC101CState),
        VMSTATE_UINT8(core.phase, ADC101CState),
        VMSTATE_BOOL(core.expect_pointer, ADC101CState),
        VMSTATE_BOOL(core.powered, ADC101CState),
        VMSTATE_BOOL(core.automatic_sample, ADC101CState),
        VMSTATE_BOOL_V(saved_reference_supply_present, ADC101CState, 2),
        VMSTATE_END_OF_LIST()
    },
};

static const Property adc101c_properties[] = {
    DEFINE_PROP_BOOL("reference-supply-present", ADC101CState,
                     reference_supply_present, true),
};

static void adc101c_class_init(ObjectClass *klass, const void *data)
{
    I2CSlaveClass *ic = I2C_SLAVE_CLASS(klass);
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);
    ic->event = adc101c_event;
    ic->recv = adc101c_rx;
    ic->send = adc101c_tx;
    rc->phases.enter = adc101c_reset_enter;
    dc->vmsd = &adc101c_vmstate;
    device_class_set_props(dc, adc101c_properties);
}

static const TypeInfo adc101c_type = {
    .name = TYPE_ADC101C,
    .parent = TYPE_I2C_SLAVE,
    .instance_size = sizeof(ADC101CState),
    .instance_init = adc101c_init_obj,
    .instance_finalize = adc101c_finalize,
    .class_init = adc101c_class_init,
};

static void adc101c_register(void)
{
    type_register_static(&adc101c_type);
}
type_init(adc101c_register);
