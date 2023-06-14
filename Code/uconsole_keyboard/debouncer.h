#ifndef DEBOUNCER_H
#define DEBOUNCER_H

#include <cstdint>

typedef uint8_t millis_t;

const millis_t DEBOUNCE_MS = 5;

/**
   @brief Asymmetric debouncer
*/
class Debouncer {
  public:
    Debouncer();
    void updateTime(millis_t delta);
    bool sample(bool value);
  private:
    millis_t timeout;
};

template<typename T, T millis>
class Timeout {
  public:
    Timeout() {
      timeout = 0;
    }

    void updateTime(millis_t delta) {
      if (timeout > delta) {
        timeout -= delta;
      } else {
        timeout = 0;
      }
    }

    void expire() {
      timeout = 0;
    }

    bool get() const {
      return timeout == 0;
    }

    void reset() {
      timeout = millis;
    }
  private:
    uint16_t timeout;
};


#endif
