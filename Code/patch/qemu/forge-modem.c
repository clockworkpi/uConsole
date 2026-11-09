/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Forge synthetic SIM7600 composite prototype: serial and RNDIS, no RF. */
#include "qemu/osdep.h"
#include "hw/usb.h"
#include "hw/usb/desc.h"
#include "hw/qdev-properties.h"
#include "hw/qdev-properties-system.h"
#include "chardev/char-fe.h"
#include "migration/vmstate.h"
#include "qemu/module.h"
#include "usb-net-internal.h"

#define TYPE_FORGE_MODEM "usb-forge-modem"
#define PORTS 5
#define RX_SIZE 8192
OBJECT_DECLARE_SIMPLE_TYPE(ForgeModem, FORGE_MODEM)

typedef struct ModemPort {
    CharFrontend chr;
    USBEndpoint *in;
    uint8_t rx[RX_SIZE];
    unsigned used;
    uint8_t tx[RX_SIZE];
    unsigned tx_used;
    guint watch;
    uint16_t lines;
} ModemPort;

struct ForgeModem {
    USBNetState parent;
    ModemPort ports[PORTS];
};
static USBDeviceClass *net_parent_class;

#define IFACE(n, size) { \
    .bInterfaceNumber = n, .bNumEndpoints = 2, \
    .bInterfaceClass = 0xff, .bInterfaceSubClass = 0xff, \
    .bInterfaceProtocol = 0xff, \
    .eps = (USBDescEndpoint[]) { \
        { .bEndpointAddress = USB_DIR_IN | (n + 3), \
          .bmAttributes = USB_ENDPOINT_XFER_BULK, .wMaxPacketSize = size }, \
        { .bEndpointAddress = USB_DIR_OUT | (n + 3), \
          .bmAttributes = USB_ENDPOINT_XFER_BULK, .wMaxPacketSize = size }, \
    }, \
}
#define NETWORK(size, interval) { \
    .bInterfaceNumber = 5, .bNumEndpoints = 1, \
    .bInterfaceClass = 2, .bInterfaceSubClass = 2, \
    .bInterfaceProtocol = 0xff, \
    .ndesc = 4, .descs = (USBDescOther[]) { \
        { .data = (uint8_t[]) { 5, 0x24, 0, 0x10, 1 } }, \
        { .data = (uint8_t[]) { 5, 0x24, 1, 0, 6 } }, \
        { .data = (uint8_t[]) { 4, 0x24, 2, 0 } }, \
        { .data = (uint8_t[]) { 5, 0x24, 6, 5, 6 } }, \
    }, \
    .eps = (USBDescEndpoint[]) { \
        { .bEndpointAddress = 0x81, .bmAttributes = USB_ENDPOINT_XFER_INT, \
          .wMaxPacketSize = 8, .bInterval = interval }, \
    }, \
}, { \
    .bInterfaceNumber = 6, .bNumEndpoints = 2, .bInterfaceClass = 0x0a, \
    .eps = (USBDescEndpoint[]) { \
        { .bEndpointAddress = 0x82, .bmAttributes = USB_ENDPOINT_XFER_BULK, \
          .wMaxPacketSize = size }, \
        { .bEndpointAddress = 0x02, .bmAttributes = USB_ENDPOINT_XFER_BULK, \
          .wMaxPacketSize = size }, \
    }, \
}
#define DEVICE(size, interval) { \
    .bcdUSB = 0x0200, .bMaxPacketSize0 = 64, .bNumConfigurations = 1, \
    .confs = (USBDescConfig[]) { { .bNumInterfaces = PORTS + 2, \
        .bConfigurationValue = 2, .bmAttributes = USB_CFG_ATT_ONE, \
        .bMaxPower = 250, .nif = PORTS + 2, \
        .ifs = (USBDescIface[]) { IFACE(0,size), IFACE(1,size), \
                                IFACE(2,size), IFACE(3,size), IFACE(4,size), \
                                NETWORK(size, interval) } } }, \
}
static const USBDescDevice full = DEVICE(64, 32);
static const USBDescDevice high = DEVICE(512, 9);
static const USBDescStrings strings = {
    [1] = "Forge", [2] = "SIM7600 composite prototype",
    [10] = "forge-modem",
};
static const USBDesc desc = {
    .id = { .idVendor = 0x1e0e, .idProduct = 0x9001, .bcdDevice = 0x0001,
            .iManufacturer = 1, .iProduct = 2, .iSerialNumber = 10 },
    .full = &full, .high = &high, .str = strings,
};

static void drain(ModemPort *port);
static gboolean writable(void *unused, GIOCondition condition, void *opaque)
{
    ModemPort *port = opaque;
    port->watch = 0;
    if (!(condition & G_IO_HUP)) {
        drain(port);
    }
    return G_SOURCE_REMOVE;
}

static void drain(ModemPort *port)
{
    if (!port->tx_used || !qemu_chr_fe_backend_open(&port->chr)) {
        return;
    }
    int count = qemu_chr_fe_write(&port->chr, port->tx, port->tx_used);
    if (count > 0) {
        memmove(port->tx, port->tx + count, port->tx_used - count);
        port->tx_used -= count;
    }
    if (port->tx_used && !port->watch) {
        port->watch = qemu_chr_fe_add_watch(&port->chr, G_IO_OUT | G_IO_HUP,
                                           writable, port);
    }
}

static void clear(ModemPort *port)
{
    if (port->watch) {
        g_source_remove(port->watch);
        port->watch = 0;
    }
    port->used = port->tx_used = 0;
    port->lines = 0;
}

static void event(void *opaque, QEMUChrEvent event)
{
    ModemPort *port = opaque;
    if (event == CHR_EVENT_CLOSED) {
        clear(port);
    } else if (event == CHR_EVENT_OPENED) {
        drain(port);
    }
}

static int can_read(void *opaque)
{
    ModemPort *port = opaque;
    return RX_SIZE - port->used;
}

static void receive(void *opaque, const uint8_t *data, int size)
{
    ModemPort *port = opaque;
    assert(size >= 0 && size <= RX_SIZE - port->used);
    memcpy(port->rx + port->used, data, size);
    port->used += size;
    usb_wakeup(port->in, 0);
}

static void reset(USBDevice *dev)
{
    ForgeModem *s = FORGE_MODEM(dev);
    net_parent_class->handle_reset(dev);
    for (int i = 0; i < PORTS; i++) {
        clear(&s->ports[i]);
    }
}

static void control(USBDevice *dev, USBPacket *p, int request,
                    int value, int index, int length, uint8_t *data)
{
    ForgeModem *s = FORGE_MODEM(dev);
    if (index == PORTS &&
        (request == (ClassInterfaceOutRequest | 0x00) ||
         request == (ClassInterfaceRequest | 0x01))) {
        net_parent_class->handle_control(dev, p, request, value, index,
                                        length, data);
        return;
    }
    if (usb_desc_handle_control(dev, p, request, value, index, length, data) >= 0) {
        return;
    }
    /* Linux option/usb_wwan uses CDC SET_CONTROL_LINE_STATE per interface. */
    if (request == (ClassInterfaceOutRequest | 0x22) && index >= 0 &&
        index < PORTS && length == 0 && !(value & ~3)) {
        s->ports[index].lines = value;
        return;
    }
    p->status = USB_RET_STALL;
}

static void transfer(USBDevice *dev, USBPacket *p)
{
    ForgeModem *s = FORGE_MODEM(dev);
    unsigned endpoint = p->ep->nr;
    if (endpoint == 1 || endpoint == 2) {
        net_parent_class->handle_data(dev, p);
        return;
    }
    if (endpoint < 3 || endpoint > PORTS + 2) {
        p->status = USB_RET_STALL;
        return;
    }
    ModemPort *port = &s->ports[endpoint - 3];
    if (p->pid == USB_TOKEN_IN) {
        unsigned count = MIN(p->iov.size, port->used);
        if (!count) {
            p->status = USB_RET_NAK;
            return;
        }
        usb_packet_copy(p, port->rx, count);
        memmove(port->rx, port->rx + count, port->used - count);
        port->used -= count;
        qemu_chr_fe_accept_input(&port->chr);
    } else if (p->pid == USB_TOKEN_OUT) {
        uint8_t buffer[512];
        if (p->iov.size > sizeof(buffer)) {
            p->status = USB_RET_STALL;
            return;
        }
        if (!qemu_chr_fe_backend_open(&port->chr) ||
            p->iov.size > RX_SIZE - port->tx_used) {
            p->status = USB_RET_NAK;
            return;
        }
        usb_packet_copy(p, buffer, p->iov.size);
        memcpy(port->tx + port->tx_used, buffer, p->actual_length);
        port->tx_used += p->actual_length;
        drain(port);
    } else {
        p->status = USB_RET_STALL;
    }
}

static void realize(USBDevice *dev, Error **errp)
{
    ForgeModem *s = FORGE_MODEM(dev);
    s->parent.control_iface = PORTS;
    net_parent_class->realize(dev, errp);
    for (int i = 0; i < PORTS; i++) {
        ModemPort *port = &s->ports[i];
        port->in = usb_ep_get(dev, USB_TOKEN_IN, i + 3);
        qemu_chr_fe_set_handlers(&port->chr, can_read, receive, event, NULL,
                                 port, NULL, true);
    }
}

static void unrealize(USBDevice *dev)
{
    ForgeModem *s = FORGE_MODEM(dev);
    for (int i = 0; i < PORTS; i++) {
        clear(&s->ports[i]);
        qemu_chr_fe_deinit(&s->ports[i].chr, false);
    }
    net_parent_class->unrealize(dev);
}

static const Property properties[] = {
    DEFINE_PROP_CHR("diagnostic", ForgeModem, ports[0].chr),
    DEFINE_PROP_CHR("gnss", ForgeModem, ports[1].chr),
    DEFINE_PROP_CHR("primary", ForgeModem, ports[2].chr),
    DEFINE_PROP_CHR("secondary", ForgeModem, ports[3].chr),
    DEFINE_PROP_CHR("audio", ForgeModem, ports[4].chr),
};
/* Never silently drop external AT engine state during a VM checkpoint. */
static const VMStateDescription vmstate = {
    .name = TYPE_FORGE_MODEM, .unmigratable = true,
};

static void class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    USBDeviceClass *uc = USB_DEVICE_CLASS(klass);
    net_parent_class = USB_DEVICE_CLASS(object_class_get_parent(klass));
    uc->product_desc = "Forge composite modem prototype";
    uc->usb_desc = &desc;
    uc->attached_settable = true;
    uc->realize = realize;
    uc->unrealize = unrealize;
    uc->handle_reset = reset;
    uc->handle_control = control;
    uc->handle_data = transfer;
    dc->vmsd = &vmstate;
    device_class_set_props(dc, properties);
}
static const TypeInfo info = {
    .name = TYPE_FORGE_MODEM, .parent = TYPE_USB_NET,
    .instance_size = sizeof(ForgeModem), .class_init = class_init,
};
static void register_types(void) { type_register_static(&info); }
type_init(register_types)
