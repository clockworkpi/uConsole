/*
 * clockworkpi devterm trackball 
 */
#include "keys_io_map.h"

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#include <USBComposite.h>


#include "trackball.h"

#include "ratemeter.h"
#include "glider.h"
#include "math.h"


 
enum Axis: uint8_t {
  AXIS_X,
  AXIS_Y,
  AXIS_NUM,
};
static TrackballMode lastMode;
static int8_t distances[AXIS_NUM];
static RateMeter rateMeter[AXIS_NUM];
static Glider glider[AXIS_NUM];

static const int8_t WHEEL_DENOM = 2;
static int8_t wheelBuffer;

static float rateToVelocityCurve(float input) {
  //return std::pow(std::abs(input) / 50, 1.4);
  return std::abs(input) / 30;
}

template<Axis AXIS, int8_t Direction >
static void interrupt( ) {
  distances[AXIS] += Direction;
  rateMeter[AXIS].onInterrupt();
  glider[AXIS].setDirection(Direction);

  const auto rx = rateMeter[AXIS_X].rate();
  const auto ry = rateMeter[AXIS_Y].rate();

  const auto rate = std::sqrt(rx * rx + ry * ry);
  const auto ratio = rateToVelocityCurve(rate) / rate;

  const auto vx = rx * ratio;
  const auto vy = ry * ratio;

  if (AXIS == AXIS_X) {
    glider[AXIS_X].update(vx, std::sqrt(rateMeter[AXIS_X].delta()));
    glider[AXIS_Y].updateSpeed(vy);
    
  } else {
    glider[AXIS_X].updateSpeed(vx);
    glider[AXIS_Y].update(vy, std::sqrt(rateMeter[AXIS_Y].delta()));

  }

 
}
 
void trackball_task(DEVTERM*dv) {
  int8_t x = 0, y = 0, w = 0;
  noInterrupts();
  //const auto mode = dv->state->moveTrackball();
  //https://forum.clockworkpi.com/t/uconsole-trackball-as-scrolling-wheel-temporary-solution/11032/3
  const auto mode = dv->Keyboard_state.fn_on == 0 ? TrackballMode::Mouse : TrackballMode::Wheel;
  if (lastMode != mode) {
    rateMeter[AXIS_X].expire();
    rateMeter[AXIS_Y].expire();
    wheelBuffer = 0;
  }
  else {
    rateMeter[AXIS_X].tick(dv->delta);
    rateMeter[AXIS_Y].tick(dv->delta);
  }
  lastMode = mode; 
  
  switch(mode){
    case TrackballMode::Mouse: {
      const auto rX = glider[AXIS_X].glide(dv->delta);
      const auto rY = glider[AXIS_Y].glide(dv->delta);
      x = rX.value;
      y = rY.value;
      if (rX.stopped) {
        glider[AXIS_Y].stop();
      }
      if (rY.stopped) {
        glider[AXIS_Y].stop();
      }

      break;
    }
    case TrackballMode::Wheel: {
      wheelBuffer += distances[AXIS_Y];
      w = wheelBuffer / WHEEL_DENOM;
      wheelBuffer -= w * WHEEL_DENOM;
      if(w != 0){
        dv->state->setScrolled();
      }
      break;
    }
  }
  distances[AXIS_X] = 0;
  distances[AXIS_Y] = 0;
  interrupts();

  if(x !=0 || y != 0 || -w!=0) {
    dv->Mouse->move(x, y, -w);
  }
 
}


void trackball_init(DEVTERM*dv){

  pinMode(LEFT_PIN, INPUT);
  pinMode(UP_PIN, INPUT);
  pinMode(RIGHT_PIN, INPUT);
  pinMode(DOWN_PIN, INPUT);

  attachInterrupt(LEFT_PIN, &interrupt<AXIS_X,-1> , ExtIntTriggerMode::CHANGE);
  attachInterrupt(RIGHT_PIN, &interrupt<AXIS_X, 1>, ExtIntTriggerMode::CHANGE);
  attachInterrupt(UP_PIN, &interrupt<AXIS_Y, -1>, ExtIntTriggerMode::CHANGE);
  attachInterrupt(DOWN_PIN, &interrupt<AXIS_Y, 1>, ExtIntTriggerMode::CHANGE);
  
}
