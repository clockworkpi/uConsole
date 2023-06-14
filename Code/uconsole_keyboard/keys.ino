#include "keys.h"

KEY_DEB keypad_debouncing;

uint8_t keys_io[ KEYS_NUM ]= {KEY1,KEY2,KEY3,KEY4,KEY5,KEY6,KEY7,KEY8,KEY9,KEY10,KEY11,KEY12,KEY13,KEY14,KEY15,KEY16,KEY0};

/* keys state(1:on, 0:off) */
static uint32_t keys;
static uint32_t keys_debouncing;
static uint32_t keys_prev;

void init_keys(){
  int i;
  for(i=0;i<KEYS_NUM;i++) {

    pinMode( keys_io[i],INPUT_PULLUP); 

  }
}

uint8_t scan_keys(){
  uint32_t data;
  uint8_t s;
  
  data = 0;
  delayMicroseconds(30);
  for(int i = 0;i < KEYS_NUM;i++) {

    s = read_io(keys_io[i]);
    s ^= 1;
    
    data |= s << i;

  }
  
  if ( keys_debouncing != data ) {
      keys_debouncing = data;
      
      keypad_debouncing.deing = true;
      keypad_debouncing.de_time = millis();
      
  }

   if (keypad_debouncing.deing == true  &&  ( (millis() - keypad_debouncing.de_time) > KEY_DEBOUNCE )) {
    keys = keys_debouncing;
    keypad_debouncing.deing = false;
  }else {
    delay(1);
  }

  return 1;
}


void print_keys(DEVTERM*dv) {
  char buff[128];

  for (int i = 0; i < KEYS_NUM; i++) {
    if( keys & (1<< i) ){
      sprintf(buff,"B%d pressed\n",i+1);
      dv->_Serial->print(buff);
    }
  }
  
  
}

void keys_task(DEVTERM*dv){
  
  scan_keys();

  uint32_t _mask =1;
  uint32_t _change = 0;
  uint32_t _pressed = 0;
  
  _change = keys ^ keys_prev;

  if(_change) {
    
    for(uint8_t c=0;c < KEYS_NUM;c++,_mask <<=1) {
      if (_change & _mask) {
        _pressed = keys & _mask;
        if(_pressed) {
          keypad_action(dv,c,KEY_PRESSED);
        }else {
          keypad_action(dv,c,KEY_RELEASED);
        }

        keys_prev ^= _mask;
      }
  
    }
  }
  
}
void keys_init(DEVTERM*dv){

  init_keys();
  //center the position
  dv->Joystick->X(511);
  dv->Joystick->Y(511);
  
  keypad_debouncing.deing = false;
  keypad_debouncing.de_time = 0;
  
}
