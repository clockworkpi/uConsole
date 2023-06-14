#ifndef MATH_H
#define MATH_H

#include <cstdint>
#include <limits>
#include <cmath>

uint32_t getDelta(uint32_t prev, uint32_t now);
uint32_t getDelta(uint32_t prev, uint32_t now, uint32_t max);

template<typename T>
T sign(T value) {
  if (value > 0) {
    return 1;
  }
  if (value < 0) {
    return -1;
  }
  return 0;
}

template<typename T, typename U>
T clamp(U value) {
  if (value >= std::numeric_limits<T>().max()) {
    return std::numeric_limits<T>().max();
  }

  if (value <= std::numeric_limits<T>().min()) {
    return std::numeric_limits<T>().min();
  }

  return value;
}

template<typename T>
T min(T x, T y) {
  if (x < y) {
      return x;
  }
  return y;
}

template<typename T>
T max(T x, T y) {
  if (x > y) {
      return x;
  }
  return y;
}

template<typename T>
T hypot(T x, T y) {
  return std::sqrt(x * x + y * y);
}

#endif
