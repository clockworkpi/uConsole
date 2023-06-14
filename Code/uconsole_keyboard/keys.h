#ifndef KEYS_H
#define KEYS_H
/*
 * keys include the joystick and mouse left/mid/right keys
 */

#include "devterm.h"
#include "keys_io_map.h"

#include <stdint.h>
#include <stdbool.h>
#include <string.h>


#ifndef KEY_DEBOUNCE
#   define KEY_DEBOUNCE 5
#endif

#define KEYS_NUM 17


void keys_task(DEVTERM*);
void keys_init(DEVTERM*);



#endif
