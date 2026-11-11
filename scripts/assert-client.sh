#!/bin/sh
# Build-time asserts for the client image. Any miss fails the build.
set -eu

fail() { echo "assert-client: FAIL: $*" >&2; exit 1; }

bin=$(command -v moonlight) || fail "moonlight not on PATH"

out=$(ldd "$bin") || fail "ldd $bin failed"
if printf '%s\n' "$out" | grep 'not found'; then
  fail "$bin has unresolved libraries"
fi
echo "assert-client: ldd clean"

QT_QPA_PLATFORM=offscreen "$bin" --version || fail "moonlight --version failed"

command -v ffmpeg >/dev/null || fail "ffmpeg CLI missing"
dec=$(ffmpeg -hide_banner -decoders) || fail "ffmpeg -decoders failed"
for pat in 'h264' 'hevc' 'av1|libdav1d|libaom-av1'; do
  printf '%s\n' "$dec" | grep -E "^ V[.A-Z]{5} ($pat)( |$)" >/dev/null \
    || fail "no ffmpeg video decoder matching $pat"
done
echo "assert-client: h264, hevc and av1 decoders present"
