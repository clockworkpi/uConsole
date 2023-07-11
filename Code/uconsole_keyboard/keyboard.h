#ifndef KEYBOARD_H
#define KEYBOARD_H

/*
 * clockworkpi devterm keyboard test2 
 * able to correct scan the 8x8 keypads re-action
 */

#include "devterm.h"

#include "keys_io_map.h"

#include <stdint.h>
#include <stdbool.h>
#include <string.h>

#define MATRIX_ROWS 8
#define MATRIX_COLS 8

#define MATRIX_KEYS 64 // 8*8

#ifndef DEBOUNCE
#   define DEBOUNCE 20
#endif

enum SKEYS {
  _SELECT_KEY =0xe8,  //Joystick.button(n)
  _START_KEY,          //Joystick.button(n)
  _JOYSTICK_UP, //B1 //Joystick.Y()
  _JOYSTICK_DOWN,    //Joystick.Y()
  _JOYSTICK_LEFT,    //Joystick.X()
  _JOYSTICK_RIGHT,   //Joystick.X()  
  _JOYSTICK_A,       //Joystick.button(1)
  _JOYSTICK_B,       //Joystick.button(2)
  _JOYSTICK_X,       //Joystick.button(3)
  _JOYSTICK_Y,       //Joystick.button(4)
  _JOYSTICK_L,
  _JOYSTICK_R,
  _FN_KEY,
  _MOUSE_LEFT,    // Mouse.press(1)
  _MOUSE_MID,     // Mouse.press(2)
  _MOUSE_RIGHT,   // Mouse.press(3)

  _FN_BRIGHTNESS_UP, //USB Consumer brightness up https://github.com/torvalds/linux/blob/7fe10096c1508c7f033d34d0741809f8eecc1ed4/drivers/hid/hid-input.c#L903
  _FN_BRIGHTNESS_DOWN, //USB Consumer brightness down 

  _VOLUME_M,
  _VOLUME_P,
  _VOLUME_MUTE, //https://github.com/torvalds/linux/blob/7fe10096c1508c7f033d34d0741809f8eecc1ed4/drivers/hid/hid-input.c#L956
  _TRACKBALL_BTN,
  _FN_LOCK_KEYBOARD,
  _FN_LIGHT_KEYBOARD,
  _FN_SHIFT //FN+SHIFT,ready for more function key
};

void init_rows();
void init_cols();
uint8_t read_io(uint8_t io);

void matrix_init();
uint8_t matrix_scan(void);

bool matrix_is_on(uint8_t row, uint8_t col);
uint8_t matrix_get_row(uint8_t row) ;


//void matrix_print(void);



void keyboard_task(DEVTERM*);
void keyboard_init(DEVTERM*);


#define KEY_PRESSED 1
#define KEY_RELEASED 0

#define KEY_PRNT_SCRN 0xCE //Print screen - 0x88 == usb hut1_12v2.pdf keyboard code
#define KEY_PAUSE  0xd0 // - 0x88 == usb hut1_12v2.pdf keyboard code

#define KEY_VOLUME_UP 0x108  // - 0x88 == usb hut1_12v2.pdf keyboard code
#define KEY_VOLUME_DOWN 0x109 //  - 0x88 == usb hut1_12v2.pdf keyboard code

#endif
