#!/bin/bash
set -euo pipefail
DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if (( $# > 2 )); then
    echo "Usage: $0 [firmware.bin] [serial_port]" >&2
    exit 2
fi
firmware=${1:-$DIR/uconsole_keyboard.ino.bin}
serial_port=${2:-}
if [[ -z $serial_port ]]; then
    if [[ $(uname -s) == Darwin ]]; then
        ports=(/dev/cu.usbmodem*)
        if [[ -e ${ports[0]} ]]; then
            serial_port=${ports[0]}
        else
            echo "No /dev/cu.usbmodem device found; pass the serial device as the second argument." >&2
            exit 2
        fi
    else
        serial_port=ttyACM0
    fi
fi
if (( EUID == 0 )); then
    exec "$DIR/maple_upload" "$serial_port" 2 1EAF:0003 "$firmware"
else
    exec sudo "$DIR/maple_upload" "$serial_port" 2 1EAF:0003 "$firmware"
fi
