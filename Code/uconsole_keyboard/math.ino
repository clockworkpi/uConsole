#include <limits>

#include "math.h"

uint32_t getDelta(uint32_t prev, uint32_t now) {
  uint32_t delta;
  if (now >= prev) {
    delta = now - prev;
  } else {
    delta = std::numeric_limits<uint32_t>().max() - prev - now + 1;
  }
  return delta;
}

uint32_t getDelta(uint32_t prev, uint32_t now, uint32_t max) {
  const auto delta = getDelta(prev, now);

  if (delta < max) {
    return delta;
  }

  return max;
}
