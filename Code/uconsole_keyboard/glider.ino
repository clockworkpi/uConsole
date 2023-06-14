#include <cmath>

#include "glider.h"
#include "math.h"

Glider::Glider()
: speed(0),
  sustain(0),
  release(0),
  error(0)
{}

void Glider::setDirection(int8_t direction) {
  if (this->direction != direction) {
    stop();
  }
  this->direction = direction;
}

void Glider::update(float speed, uint16_t sustain) {
  this->speed = speed;
  this->sustain = sustain;
  this->release = sustain;
}

void Glider::updateSpeed(float speed) {
  this->speed = speed;
}

void Glider::stop() {
  this->speed = 0;
  this->sustain = 0;
  this->release = 0;
  this->error = 0;
}

Glider::GlideResult Glider::glide(millis_t delta) {
  const auto alreadyStopped = speed == 0;

  error += speed * delta;
  int8_t distance = 0;
  if (error > 0) {
    distance = clamp<int8_t>(std::ceil(error));
  }
  error -= distance;

  if (sustain > 0) {
    const auto sustained = min(sustain, (uint16_t)delta);
    sustain -= sustained;
  } else if (release > 0) {
    const auto released = min(release, (uint16_t)delta);
    speed = speed * (release - released) / release;
    release -= released;
  } else {
    speed = 0;
  }

  const int8_t result = direction * distance;
  return GlideResult { result, !alreadyStopped && speed == 0 };
}
