#include "keyboard.h"
#include "helper.h"

KEY_DEB keyboard_debouncing;

uint8_t matrix_rows[ MATRIX_ROWS ]= {ROW1,ROW2,ROW3,ROW4,ROW5,ROW6,ROW7,ROW8};
uint8_t matrix_cols[ MATRIX_COLS ] = {COL1,COL2,COL3,COL4,COL5,COL6,COL7,COL8};

/* matrix state(1:on, 0:off) */
static uint8_t matrix[MATRIX_ROWS];
static uint8_t matrix_debouncing[MATRIX_COLS];
static uint8_t matrix_prev[MATRIX_ROWS];

uint8_t read_kbd_io(uint8_t io) {

#if defined KEYBOARD_PULL  && KEYBOARD_PULL  == 0  
  if(digitalRead(io) == LOW ){
    return 0;
  }else {
    return 1;
  }
#elif defined KEYBOARD_PULL  && KEYBOARD_PULL  == 1 
   if(digitalRead(io) == LOW ){
    return 1;
  }else {
    return 0;
  }
#endif

}

void init_rows(){
  int i;
  for(i=0;i<8;i++) {
    
#if defined KEYBOARD_PULL  && KEYBOARD_PULL  == 0   
    pinMode(matrix_rows[i],OUTPUT);
    digitalWrite(matrix_rows[i],LOW);
    pinMode(matrix_rows[i],INPUT_PULLDOWN);
    
#elif defined KEYBOARD_PULL  && KEYBOARD_PULL  == 1 
    pinMode(matrix_rows[i],INPUT_PULLUP);
#endif
  
  }
}

void init_cols() {

    int i;
    for(i=0;i<8;i++){
      pinMode(matrix_cols[i],OUTPUT);
      digitalWrite(matrix_cols[i],LOW);
    }
}



void matrix_init() {
  init_cols();
  init_rows();
  
  for (uint8_t i=0; i < MATRIX_ROWS; i++) {
    matrix[i] = 0;
    matrix_debouncing[i] = 0;
    matrix_prev[i] = 0;
  }
 
  
  delay(500);
}

uint8_t matrix_scan(void) {

  uint8_t data;
  for(int col = 0; col < MATRIX_COLS;col++){
    data = 0;
    
#if defined KEYBOARD_PULL  && KEYBOARD_PULL  == 1

    digitalWrite(matrix_cols[col],LOW);
#elif defined KEYBOARD_PULL  && KEYBOARD_PULL  == 0
    digitalWrite(matrix_cols[col],HIGH);

#endif
    delayMicroseconds(700);

    data =(
          ( read_kbd_io(matrix_rows[0]) << 0 )  |
          ( read_kbd_io(matrix_rows[1]) << 1 )  |
          ( read_kbd_io(matrix_rows[2]) << 2 )  |
          ( read_kbd_io(matrix_rows[3]) << 3 )  |
          ( read_kbd_io(matrix_rows[4]) << 4 )  |
          ( read_kbd_io(matrix_rows[5]) << 5 )  | 
          ( read_kbd_io(matrix_rows[6]) << 6 )  |
          ( read_kbd_io(matrix_rows[7]) << 7 )  
    );

#if defined KEYBOARD_PULL  && KEYBOARD_PULL  == 1
    digitalWrite(matrix_cols[col],HIGH);
#elif defined KEYBOARD_PULL  && KEYBOARD_PULL  == 0
    digitalWrite(matrix_cols[col],LOW);
#endif

    if (matrix_debouncing[col] != data) {
      matrix_debouncing[col] = data;
      keyboard_debouncing.deing = true;
      keyboard_debouncing.de_time = millis();
    }
  }

  if (keyboard_debouncing.deing == true  &&  ( (millis() - keyboard_debouncing.de_time) > DEBOUNCE )) {
    for (int row = 0; row < MATRIX_ROWS; row++) {
            
      matrix[row] = 0;
      for (int col = 0; col < MATRIX_COLS; col++) {
        matrix[row] |= ((matrix_debouncing[col] & (1 << row) ? 1 : 0) << col);
      }
     }
    keyboard_debouncing.deing = false;
    
  }else{
    delay(1);
  }
  
  return 1;
}


bool matrix_is_on(uint8_t row, uint8_t col) {
    return (matrix[row] & (1<<col));
}



uint8_t matrix_get_row(uint8_t row) {
    return matrix[row];
}


void matrix_press(DEVTERM*dv,uint8_t row,uint8_t col) {
  char buff[128];

  if(matrix_is_on(row,col) == true ){
    sprintf(buff,"%d %d M%d pressed\n",row,col,(row+1)*10+col+1);
    //dv->_Serial->print(buff);
    keyboard_action(dv,row,col,KEY_PRESSED);
  }
  
}

void matrix_release(DEVTERM*dv,uint8_t row,uint8_t col) {
  char buff[128];

  
  if(matrix_is_on(row,col) == false ){
    sprintf(buff,"%d %d M%d released\n",row,col,(row+1)*10+col+1);
    //dv->_Serial->print(buff);
    keyboard_action(dv,row,col,KEY_RELEASED);
        
  }
  
}

void keyboard_task(DEVTERM*dv)
{
 char buff[128];
  uint8_t matrix_row = 0;
  uint8_t matrix_change = 0;
  uint8_t pressed = 0;
  
  matrix_scan();
  
  for (uint8_t r = 0; r < MATRIX_ROWS; r++) {
    matrix_row = matrix_get_row(r);
    matrix_change = matrix_row ^ matrix_prev[r];
    if (matrix_change) { 
      //sprintf(buff,"matrix_row: %d %d\n",matrix_row,matrix_prev[r]);
      //dv->_Serial->print(buff);
      uint8_t col_mask = 1;
      for (uint8_t c = 0; c < MATRIX_COLS; c++, col_mask <<= 1) {
        if (matrix_change & col_mask) {
          pressed = (matrix_row & col_mask); 
          if(pressed != 0) {
            matrix_press(dv,r,c);
          }else {
            matrix_release(dv,r,c);
          }
          matrix_prev[r] ^= col_mask;

         }
      }
    }
  }
}

void keyboard_init(DEVTERM*){
  matrix_init();
  keyboard_debouncing.deing=false;
  keyboard_debouncing.de_time = 0;
  
}
