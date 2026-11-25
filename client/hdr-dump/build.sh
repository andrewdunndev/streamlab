#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later
# Builds hdr-dump against the moonlight-common-c checkout that moonlight-qt
# pins, with the same source list and defines as moonlight-qt's own build.
# usage: build.sh COMMON_C_DIR OUTPUT
set -eu

src=$1
out=$2
here=$(cd "$(dirname "$0")" && pwd)
rev=$(git -C "$src" rev-parse --short=7 HEAD)
# The SEI tap: common-c drops prepended SEI NALs, so criterion 3 reads a copy
# taken before they are dropped. The checkout is a throwaway build tree.
git -C "$src" apply "$here/common-c-sei-tap.patch"
obj=$(mktemp -d)
trap 'rm -rf "$obj"' EXIT

inc="-I$src/src -I$src/enet/include -I$src/nanors -I$src/nanors/deps -I$src/nanors/deps/obl"
defs="-DNDEBUG -DHAS_SOCKLEN_T -DHAVE_CLOCK_GETTIME=1"

for f in "$src"/src/*.c "$src"/enet/callbacks.c "$src"/enet/compress.c "$src"/enet/host.c \
  "$src"/enet/list.c "$src"/enet/packet.c "$src"/enet/peer.c "$src"/enet/protocol.c \
  "$src"/enet/unix.c "$src"/nanors/rs.c "$src"/nanors/deps/obl/oblas_common.c \
  "$src"/nanors/deps/obl/oblas_lite.c; do
  # shellcheck disable=SC2086
  gcc -O2 -std=gnu99 -pthread $defs $inc -c "$f" -o "$obj/$(basename "$f" .c).o"
done

# shellcheck disable=SC2086
gcc -O2 -std=gnu11 -Wall -Wextra -Werror -pthread $inc -DCOMMON_C_REV="\"$rev\"" \
  "$here/hdr-dump.c" "$obj"/*.o -o "$out" -lcurl -lcrypto -lpthread -lm
