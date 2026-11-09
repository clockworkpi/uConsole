/* Copyright (C) 2015 Roger Clark <www.rogerclark.net>
 * 
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 *
 * Utility to send the reset sequence on RTS and DTR and chars
 * which resets the libmaple and causes the bootloader to be run
 *
 *
 *
 * Terminal control code by Heiko Noordhof (see copyright below)
 */



/* Copyright (C) 2003 Heiko Noordhof <heikyAusers.sf.net>
 * 
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 */

#include <stdio.h>
#include <stdlib.h>
#include <termios.h>
#include <unistd.h>  
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <stdbool.h>
#include <errno.h>
#include <time.h>

/* macOS spells the combined hardware-flow-control flags separately. */
#ifndef CRTSCTS
#define CRTSCTS (CCTS_OFLOW | CRTS_IFLOW)
#endif

/* Function prototypes (belong in a seperate header file) */
int   openserial(char *devicename);
void  closeserial(void);
int   setDTR(unsigned short level);
int   setRTS(unsigned short level); 


/* Two globals for use by this module only */
static int fd = -1;
static bool terminfo_saved;
static struct termios oldterminfo;


void closeserial(void)
{
     if (fd < 0) return;
     if (terminfo_saved) tcsetattr(fd, TCSANOW, &oldterminfo);
     close(fd);
     fd = -1;
     terminfo_saved = false;
}


int openserial(char *devicename) 
{
     struct termios attr;

     if ((fd = open(devicename, O_RDWR | O_NOCTTY)) == -1) return 0; /* Error */
     atexit(closeserial);

     if (tcgetattr(fd, &oldterminfo) == -1) return 0; /* Error */
     terminfo_saved = true;
     attr = oldterminfo;
     attr.c_cflag |= CRTSCTS | CLOCAL;
     attr.c_oflag = 0;
     if (tcflush(fd, TCIOFLUSH) == -1) return 0; /* Error */
     if (tcsetattr(fd, TCSANOW, &attr) == -1) return 0; /* Error */

     /* Set the lines to a known state, and */
     /* finally return non-zero is successful. */
     return setRTS(0) && setDTR(0);
}


/* For the two functions below:
 *     level=0 to set line to LOW
 *     level=1 to set line to HIGH
 */

int setRTS(unsigned short level)
{
     int status;

     if (ioctl(fd, TIOCMGET, &status) == -1) {
	  perror("setRTS(): TIOCMGET");
	  return 0;
     }
     if (level) status |= TIOCM_RTS;
     else status &= ~TIOCM_RTS;
     if (ioctl(fd, TIOCMSET, &status) == -1) {
	  perror("setRTS(): TIOCMSET");
	  return 0;
     }
     return 1;
}


int setDTR(unsigned short level)
{
     int status;

     if (ioctl(fd, TIOCMGET, &status) == -1) {
	  perror("setDTR(): TIOCMGET");
	  return 0;
     }
     if (level) status |= TIOCM_DTR;
     else status &= ~TIOCM_DTR;
     if (ioctl(fd, TIOCMSET, &status) == -1) {
	  perror("setDTR: TIOCMSET");
	  return 0;
     }
     return 1;
}

/* This portion of code was written by Roger Clark
 * It was informed by various other pieces of code written by Leaflabs to reset their 
 * Maple and Maple mini boards 
 */

static int delay_ms(long milliseconds)
{
    struct timespec remaining = {
        .tv_sec = milliseconds / 1000,
        .tv_nsec = (milliseconds % 1000) * 1000000L
    };
    while (nanosleep(&remaining, &remaining) == -1) {
        if (errno != EINTR) {
            perror("nanosleep");
            return 0;
        }
    }
    return 1;
}

int main(int argc, char *argv[])
{
    long delay = 0;
    char *end;
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "Usage: upload-reset <serial_device> [delay_ms: 0..60000]\n");
        return EXIT_FAILURE;
    }
    if (argc == 3) {
        errno = 0;
        delay = strtol(argv[2], &end, 10);
        if (errno || end == argv[2] || *end || delay < 0 || delay > 60000) {
            fprintf(stderr, "Invalid delay: expected 0..60000 milliseconds\n");
            return EXIT_FAILURE;
        }
    }
    if (!openserial(argv[1])) {
        fprintf(stderr, "Failed to initialize serial device: %s\n", argv[1]);
        return EXIT_FAILURE;
    }

    /* Send the bootloader reset sequence. Stop if any operation fails. */
    if (!setRTS(false) || !setDTR(false) || !setDTR(true) || !delay_ms(50) ||
        !setDTR(false) || !setRTS(true) || !setDTR(true) || !delay_ms(50) ||
        !setDTR(false) || !delay_ms(50)) {
        return EXIT_FAILURE;
    }

    const char magic[] = "1EAF";
    size_t sent = 0;
    while (sent < sizeof(magic) - 1) {
        ssize_t count = write(fd, magic + sent, sizeof(magic) - 1 - sent);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) {
            fprintf(stderr, "Failed to write bootloader reset sequence\n");
            return EXIT_FAILURE;
        }
        sent += (size_t)count;
    }
    closeserial();
    return delay_ms(delay) ? EXIT_SUCCESS : EXIT_FAILURE;
}
