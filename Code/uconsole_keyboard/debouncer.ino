#include "debouncer.h"

Debouncer::Debouncer()
  : timeout(0)
{
}

void Debouncer::updateTime(millis_t delta) {
  if (timeout > delta) {
    timeout -= delta;
  } else {
    timeout = 0;
  }
}

bool Debouncer::sample(bool value) {
  if (value || timeout == 0) {
    timeout = DEBOUNCE_MS;
    return true;
  }

  return false;
}
