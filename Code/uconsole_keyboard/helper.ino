#include "helper.h"


uint8_t read_io(uint8_t io) {
  if(digitalRead(io) == LOW ){
    return 0;
  }else {
    return 1;
  }
}
