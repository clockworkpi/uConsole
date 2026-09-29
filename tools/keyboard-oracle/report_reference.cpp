// Host shim for differential testing of the unmodified pinned USBComposite
// Keyboard.cpp, Mouse.cpp, Consumer.cpp and Joystick.cpp implementations.
// Only object layout/construction and USB sending are replaced here.
#define _USBCOMPOSITE_H_
#include <cstdint>
#include <cstring>
#include <iostream>
#include <iomanip>
#include <sstream>
#include <string>

#define HID_KEYBOARD_ROLLOVER 6
static void emit(const void *data, unsigned size) {
    const auto *bytes = static_cast<const uint8_t *>(data);
    for (unsigned i = 0; i < size; ++i)
        std::cout << std::hex << std::setfill('0') << std::setw(2) << unsigned(bytes[i]);
    std::cout << '\n';
}
struct USBHID { void addOutputBuffer(int *) {} } HID;
struct __attribute__((packed)) KeyReport {
    uint8_t id = 2, modifiers = 0, reserved = 0, keys[6] = {};
};
class HIDKeyboard {
public:
    KeyReport keyReport;
    int ledData = 0;
    uint8_t leds = 0;
    bool adjustForHostCapsLock = true;
    uint8_t getLEDs() { return leds; }
    void sendReport() { emit(&keyReport, sizeof(keyReport)); }
    uint8_t getKeyCode(uint8_t, uint8_t *);
    void begin(); void end(); void releaseAll();
    size_t press(uint8_t); size_t release(uint8_t); size_t write(uint8_t);
};
class HIDMouse {
public:
    uint8_t _buttons = 0, reportBuffer[5] = {1, 0, 0, 0, 0};
    void sendReport() { emit(reportBuffer, sizeof(reportBuffer)); }
    void begin(); void end(); void buttons(uint8_t); void click(uint8_t);
    void press(uint8_t); void release(uint8_t); bool isPressed(uint8_t);
    void move(signed char, signed char, signed char);
};
struct __attribute__((packed)) ConsumerReport { uint8_t id = 3; uint16_t button = 0; };
class HIDConsumer {
public:
    ConsumerReport report;
    void sendReport() { emit(&report, sizeof(report)); }
    void begin(); void end(); void press(uint16_t); void release();
};
struct __attribute__((packed)) JoystickReport {
    uint8_t id;
    uint32_t buttons;
    unsigned hat:4, x:10, y:10, rx:10, ry:10, sliderLeft:10, sliderRight:10;
};
static_assert(sizeof(JoystickReport) == 13, "Unsupported host bitfield layout");
class HIDJoystick {
public:
    JoystickReport joyReport = {20, 0, 15, 512, 512, 512, 512, 0, 0};
    bool manualReport = false;
    void sendReport() { emit(&joyReport, sizeof(joyReport)); }
    void begin(); void end(); void safeSendReport();
    void setManualReportMode(bool); bool getManualReportMode();
    void button(uint8_t, bool); void X(uint16_t); void Y(uint16_t);
    void position(uint16_t, uint16_t); void Xrotate(uint16_t); void Yrotate(uint16_t);
    void sliderLeft(uint16_t); void sliderRight(uint16_t); void slider(uint16_t); void hat(int16_t);
};

// These resolve through the explicitly selected USBComposite source directory.
// The guard above suppresses platform headers, not the report method bodies.
#include <Keyboard.cpp>
#include <Mouse.cpp>
#include <Consumer.cpp>
#include <Joystick.cpp>

int main() {
    HIDKeyboard keyboard;
    HIDMouse mouse;
    HIDConsumer consumer;
    HIDJoystick joystick;
    std::string name;
    int a, b, c;
    while (std::cin >> name >> a >> b >> c) {
        if (name == "keyboard_press") keyboard.press(a);
        else if (name == "keyboard_release") keyboard.release(a);
        else if (name == "caps_adjust") keyboard.adjustForHostCapsLock = a;
        else if (name == "leds") keyboard.leds = a;
        else if (name == "mouse_press") mouse.press(a);
        else if (name == "mouse_release") mouse.release(a);
        else if (name == "mouse_click") mouse.click(a);
        else if (name == "mouse_move") mouse.move(a, b, c);
        else if (name == "consumer_press") consumer.press(a);
        else if (name == "consumer_release") consumer.release();
        else if (name == "joystick_x") joystick.X(a);
        else if (name == "joystick_y") joystick.Y(a);
        else if (name == "joystick_button") joystick.button(a, b);
        else if (name != "pwm" && name != "state") return 2;
    }
    return std::cin.eof() ? 0 : 2;
}
