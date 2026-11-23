#!/bin/sh
# Plug a PS pad with motion caps, inject a gyro vector, read it back in the app container.
# Run on the Wolf host as root, one session live: example-gyro.sh APP_CONTAINER [X Y Z]
set -eu
app=$1; x=${2:-10}; y=${3:-20}; z=${4:-30}
here=$(dirname "$0")
python3 "$here/pad.py" plug 0 ps 0xbf
podman exec -i "$app" python3 -I - motion 4 < "$here/probe.py" > "${TMPDIR:-/tmp}/gyro.out" &
sleep 1.5; python3 "$here/pad.py" motion 0 gyro "$x" "$y" "$z"; wait
echo "expected: gyro $x $y $z deg/s (hidraw gyro is raw int16, evdev is hid-playstation scaled)"
echo "read:"; grep -E ' (hid|evm) ' "${TMPDIR:-/tmp}/gyro.out" | tail -n 4
python3 "$here/pad.py" unplug 0
