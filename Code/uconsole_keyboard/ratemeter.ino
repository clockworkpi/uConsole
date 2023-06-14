#include <Arduino.h>
#include <cstdint>

#include "ratemeter.h"
#include "math.h"

RateMeter::RateMeter()
: lastTime(0)
{}

void RateMeter::onInterrupt() {
  const auto now = millis();
  if (cutoff.get()) {
    averageDelta = CUTOFF_MS;
  } else {
    const auto delta = getDelta(lastTime, now, CUTOFF_MS);
    averageDelta = (averageDelta + delta) / 2;
  }
  lastTime = now;
  cutoff.reset();
}

void RateMeter::tick(millis_t delta) {
  cutoff.updateTime(delta);
  if (!cutoff.get()) {
    averageDelta += delta;
  }
}

void RateMeter::expire() {
  cutoff.expire();
}

uint16_t RateMeter::delta() const {
  return averageDelta;
}

float RateMeter::rate() const {
  if (cutoff.get()) {
    return 0.0f;
  } else if (averageDelta == 0) {
    // to ensure range 0 ~ 1000.0
    return 1000.0f;
  } else {
    return 1000.0f / (float)averageDelta;
  }
}
