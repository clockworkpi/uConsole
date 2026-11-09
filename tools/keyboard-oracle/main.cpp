// Execute the checked-in firmware on logical pins with deterministic time.
// Records are USBComposite API calls, not packets or hardware measurements.
#include "Arduino.h"
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <algorithm>

#include "../../Code/uconsole_keyboard/uconsole_keyboard.ino"
void keyboard_action(DEVTERM *, uint8_t, uint8_t, uint8_t);
void keypad_action(DEVTERM *, uint8_t, uint8_t);
#include "../../Code/uconsole_keyboard/helper.ino"
#include "../../Code/uconsole_keyboard/keymaps.ino"
#include "../../Code/uconsole_keyboard/keyboard.ino"
#include "../../Code/uconsole_keyboard/keys.ino"
#include "../../Code/uconsole_keyboard/math.ino"
#include "../../Code/uconsole_keyboard/debouncer.ino"
#include "../../Code/uconsole_keyboard/state.ino"
#include "../../Code/uconsole_keyboard/ratemeter.ino"
#include "../../Code/uconsole_keyboard/glider.ino"
#include "../../Code/uconsole_keyboard/trackball.ino"

uint64_t oracle_us = 0;
static bool contacts[8][8] = {};
static bool direct_keys[KEYS_NUM] = {};
static int outputs[PIN_COUNT] = {};
static bool mode_switch = true;
static void (*handlers[PIN_COUNT])() = {};

void oracle_event(const char *event, int a, int b, int c) {
    std::cout << "{\"us\":" << oracle_us << ",\"event\":\"" << event
              << "\",\"args\":[" << a << ',' << b << ',' << c << "]}\n";
}
uint32_t millis() { return static_cast<uint32_t>(oracle_us / 1000); }
void delay(uint32_t ms) { oracle_us += uint64_t(ms) * 1000; }
void delayMicroseconds(uint32_t us) { oracle_us += us; }
void pinMode(uint8_t, int) {}
void digitalWrite(uint8_t pin, int value) { outputs[pin] = value; }
void pwmWrite(uint8_t pin, int value) { oracle_event("pwm", pin, value); }
void attachInterrupt(uint8_t pin, void (*handler)(), ExtIntTriggerMode) {
    handlers[pin] = handler;
}
int digitalRead(uint8_t pin) {
    if (pin == PD2) return mode_switch ? HIGH : LOW;
    for (unsigned i = 0; i < KEYS_NUM; ++i)
        if (pin == keys_io[i]) return direct_keys[i] ? LOW : HIGH;
    for (unsigned row = 0; row < 8; ++row) {
        if (pin != matrix_rows[row]) continue;
        for (unsigned col = 0; col < 8; ++col)
            if (outputs[matrix_cols[col]] == HIGH && contacts[row][col]) return HIGH;
        return LOW;
    }
    throw std::runtime_error("unmodeled digitalRead pin");
}

static int number(std::istringstream &input, int maximum) {
    int n;
    if (!(input >> n) || n < 0 || n > maximum)
        throw std::runtime_error("argument outside supported range");
    return n;
}
static void end(std::istringstream &input) {
    std::string extra;
    if (input >> extra) throw std::runtime_error("unexpected argument");
}
int main() {
    try {
        std::cout << "{\"evidence\":\"host-firmware-semantic-oracle\",\"schema\":1}\n";
        setup();
        std::string line;
        while (std::getline(std::cin, line)) {
            std::istringstream input(line);
            std::string command;
            if (!(input >> command)) continue;
            if (command == "matrix") {
                int r = number(input, 7), c = number(input, 7), value = number(input, 1);
                end(input);
                contacts[r][c] = value;
            } else if (command == "key") {
                int k = number(input, 16), value = number(input, 1);
                end(input);
                direct_keys[k] = value;
            } else if (command == "switch") {
                int value = number(input, 1);
                end(input);
                mode_switch = value;
            } else if (command == "run") {
                int count = number(input, 10000);
                end(input);
                while (count--) loop();
            } else if (command == "advance") {
                int ms = number(input, 1000000);
                end(input);
                delay(ms);
            } else if (command == "edge") {
                int direction = number(input, 3);
                end(input);
                const uint8_t pins[] = {LEFT_PIN, RIGHT_PIN, UP_PIN, DOWN_PIN};
                handlers[pins[direction]]();
            } else if (command == "state") {
                end(input);
                oracle_event("state", dev_term.Keyboard_state.fn_on,
                             dev_term.Keyboard_state.lock, dev_term.Keyboard_state.backlight);
            } else if (command == "sync") {
                std::string token;
                input >> token;
                end(input);
                if (token.size() != 32 || !std::all_of(token.begin(), token.end(), [](char c) {
                        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f');
                    })) throw std::runtime_error("invalid sync token");
                std::cout << "{\"sync\":\"" << token << "\",\"us\":" << oracle_us << "}\n";
                std::cout.flush();
            } else {
                throw std::runtime_error("unknown command");
            }
        }
        delete dev_term.Keyboard;
        delete dev_term.Joystick;
        delete dev_term.Mouse;
        delete dev_term.Consumer;
        delete dev_term._Serial;
        delete dev_term.state;
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "keyboard oracle: " << error.what() << '\n';
        return 2;
    }
}
