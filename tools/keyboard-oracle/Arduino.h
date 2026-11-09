// Host-only logical GPIO/time boundary. Pin numbers are local identifiers,
// not STM32 register addresses. Production firmware is included unchanged.
#pragma once
#include <cstdint>
#include <cstdio>

enum {
    PA0, PA1, PA2, PA3, PA4, PA5, PA6, PA7, PA8,
    PB0, PB1, PB2, PB3, PB4, PB5, PB6, PB7,
    PB8, PB9, PB10, PB11, PB12, PB13, PB14, PB15,
    PC0, PC1, PC2, PC3, PC4, PC5, PC6, PC7,
    PC8, PC9, PC10, PC11, PC12, PD2, PIN_COUNT
};
enum { LOW, HIGH, INPUT, OUTPUT, INPUT_PULLUP, INPUT_PULLDOWN, PWM };
enum class ExtIntTriggerMode { CHANGE };
extern uint64_t oracle_us;
void oracle_event(const char *event, int a = 0, int b = 0, int c = 0);
uint32_t millis();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
int digitalRead(uint8_t pin);
void digitalWrite(uint8_t pin, int value);
void pinMode(uint8_t pin, int mode);
void pwmWrite(uint8_t pin, int value);
void attachInterrupt(uint8_t pin, void (*handler)(), ExtIntTriggerMode mode);
inline void noInterrupts() {}
inline void interrupts() {}
struct HardwareTimer {
    explicit HardwareTimer(int) {}
    void setPeriod(int) {}
    void resume() {}
};
