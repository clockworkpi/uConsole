#ifndef GLIDER_H
#define GLIDER_H

#include <cstdint>


class Glider {
public:
  Glider();
  void setDirection(int8_t);
  void update(float velocity, uint16_t sustain);
  void updateSpeed(float velocity);
  void stop();

  struct GlideResult {
    int8_t value;
    bool stopped;
  };
  GlideResult glide(uint8_t delta);

public:
  int8_t direction;
  float speed;
  uint16_t sustain;
  uint16_t release;
  float error;
};

#endif
