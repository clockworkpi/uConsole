#ifndef RATEMETER_H
#define RATEMETER_H

#include <cstdint>

#include "debouncer.h"

class RateMeter {
public:
  RateMeter();
  void onInterrupt();
  void tick(millis_t delta);
  void expire();

  uint16_t delta() const;
  // Hall sensor edges per seconds.
  // stopped: 0
  // really slow => ~3
  // medium => ~30
  // fast => < 300
  // max => 1000
  float rate() const;

private:
  uint32_t lastTime;

  // really Range, emperically:
  // fast => < 5_000 us,
  // medium => 20_000 - 40_000 us
  // really slow => 250_000 us
  uint32_t averageDelta;

  static const uint16_t CUTOFF_MS = 1000;
  // Cut off after some seconds to prevent multiple timestamp overflow (~70 mins)
  Timeout<uint16_t, CUTOFF_MS> cutoff;
};

#endif
