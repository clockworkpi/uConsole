#include <cassert>
#include <algorithm>
#include <limits>

#include "state.h"

State::State()
  : fn(false),
    middleClick(false),
    scrolled(false)
{
}

void State::tick(millis_t delta)
{
  middleClickTimeout.updateTime(delta);
}
void State::setScrolled() {
  if(middleClick==true){
    scrolled = true;
  }
}

bool State::getScrolled() {
  return scrolled;
}

void State::pressMiddleClick() {
  middleClick = true;
  middleClickTimeout.reset();
}

bool State::releaseMiddleClick() {
  middleClick = false;
  scrolled = false;
  const auto timeout = middleClickTimeout.get();
  return !timeout;
}

TrackballMode State::moveTrackball() {
  middleClickTimeout.expire();
  if (middleClick) {
    return TrackballMode::Wheel;
  } else {
    return TrackballMode::Mouse;
  }
}
