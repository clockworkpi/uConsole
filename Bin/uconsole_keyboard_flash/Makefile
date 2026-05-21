CC=gcc
CFLAGS=-I.
DEPS = 
OBJ = upload-reset/upload-reset.o

%.o: %.c $(DEPS)
	$(CC) -c -o $@ $< $(CFLAGS)

upload-reset.elf: $(OBJ)
	$(CC) -o $@ $^ $(CFLAGS)


clean:
	rm upload-reset/*.o upload-reset.elf
