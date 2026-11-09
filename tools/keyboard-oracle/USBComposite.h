// Semantic call recorder, NOT an implementation of USBComposite or USB HID.
// API key/consumer constants match the pinned STM32F1 2021.2.22 USBHID.h.
#pragma once
#include "Arduino.h"

enum {
    KEY_LEFT_CTRL = 0x80, KEY_LEFT_SHIFT, KEY_LEFT_ALT, KEY_LEFT_GUI,
    KEY_RIGHT_CTRL, KEY_RIGHT_SHIFT, KEY_RIGHT_ALT, KEY_RIGHT_GUI,
    KEY_RETURN = 0xb0, KEY_ESC, KEY_BACKSPACE, KEY_TAB,
    KEY_CAPS_LOCK = 0xc1, KEY_F1, KEY_F2, KEY_F3, KEY_F4, KEY_F5, KEY_F6,
    KEY_F7, KEY_F8, KEY_F9, KEY_F10, KEY_F11, KEY_F12,
    KEY_INSERT = 0xd1, KEY_HOME, KEY_PAGE_UP, KEY_DELETE, KEY_END,
    KEY_PAGE_DOWN, KEY_RIGHT_ARROW, KEY_LEFT_ARROW, KEY_DOWN_ARROW, KEY_UP_ARROW,
    MOUSE_MIDDLE = 4
};
// setup() needs descriptor expressions, but these placeholders are never
// inspected or emitted. Use keyboard_usb_contract.py for real descriptor bytes.
#define HID_CONSUMER_REPORT_DESCRIPTOR() 0
#define HID_KEYBOARD_REPORT_DESCRIPTOR() 0
#define HID_JOYSTICK_REPORT_DESCRIPTOR() 0
#define HID_MOUSE_REPORT_DESCRIPTOR() 0
struct USBCompositeSerial { void print(const char *) {} };
struct USBHID {
    void begin(USBCompositeSerial &, const uint8_t *, unsigned) {}
};
struct HIDKeyboard {
    explicit HIDKeyboard(USBHID &) {}
    void press(uint16_t k) { oracle_event("keyboard_press", k); }
    void release(uint16_t k) { oracle_event("keyboard_release", k); }
    void setAdjustForHostCapsLock(bool on) { oracle_event("caps_adjust", on); }
};
struct HIDJoystick {
    explicit HIDJoystick(USBHID &) {}
    void X(int v) { oracle_event("joystick_x", v); }
    void Y(int v) { oracle_event("joystick_y", v); }
    void button(int n, int v) { oracle_event("joystick_button", n, v); }
};
struct HIDMouse {
    explicit HIDMouse(USBHID &) {}
    void press(int k) { oracle_event("mouse_press", k); }
    void release(int k) { oracle_event("mouse_release", k); }
    void click(int k) { oracle_event("mouse_click", k); }
    void move(int x, int y, int w) { oracle_event("mouse_move", x, y, w); }
};
struct HIDConsumer {
    enum { BRIGHTNESS_UP = 0x6f, BRIGHTNESS_DOWN = 0x70,
           VOLUME_UP = 0xe9, VOLUME_DOWN = 0xea, MUTE = 0xe2 };
    explicit HIDConsumer(USBHID &) {}
    void press(int v) { oracle_event("consumer_press", v); }
    void release() { oracle_event("consumer_release"); }
};
struct CompositeStub {
    void setManufacturerString(const char *) {}
    void setProductString(const char *) {}
    void setSerialString(const char *) {}
    explicit operator bool() const { return true; }
};
inline CompositeStub USBComposite;
