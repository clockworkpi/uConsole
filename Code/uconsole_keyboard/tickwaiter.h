#ifndef TICKWAITER_H
#define TICKWAITER_H

#include <cstdint>
#include "math.h"

template<uint32_t TargetInterval>
class TickWaiter {
  public:
    uint8_t waitForNextTick() {
      const auto last = this->last;
      const auto now = millis();
      this->last = now;

      const auto delta = getDelta(last, now, 255);

      if (delta >= TargetInterval) {
        return delta;
      }

      delay(TargetInterval - delta);

      const auto now2 = millis();
      return getDelta(last, now2, 255);
    }
  private:
    uint32_t last = 0;
};

#endif
