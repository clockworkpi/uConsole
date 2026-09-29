/* SPDX-License-Identifier: MIT */
/*
 * State extracted from QEMU hw/usb/dev-network.c.
 * Copyright (c) 2006 Thomas Sailer
 * Copyright (c) 2008 Andrzej Zaborowski
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
/* Internal USB networking state shared with composite device subclasses. */
#ifndef HW_USB_NET_INTERNAL_H
#define HW_USB_NET_INTERNAL_H
#include "hw/usb.h"
#include "net/net.h"
#include "qemu/queue.h"

enum rndis_state {
    RNDIS_UNINITIALIZED,
    RNDIS_INITIALIZED,
    RNDIS_DATA_INITIALIZED,
};
struct rndis_response;
struct USBNetState {
    USBDevice dev;
    enum rndis_state rndis_state;
    uint32_t medium;
    uint32_t speed;
    uint32_t media_state;
    uint16_t filter;
    uint32_t vendorid;
    uint16_t connection;
    unsigned int out_ptr;
    uint8_t out_buf[2048];
    unsigned int in_ptr, in_len;
    uint8_t in_buf[2048];
    USBEndpoint *intr;
    USBEndpoint *bulk_in;
    char usbstring_mac[13];
    NICState *nic;
    NICConf conf;
    QTAILQ_HEAD(, rndis_response) rndis_resp;
    uint8_t control_iface;
};
#define TYPE_USB_NET "usb-net"
OBJECT_DECLARE_SIMPLE_TYPE(USBNetState, USB_NET)
#endif
