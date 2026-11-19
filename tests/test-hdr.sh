#!/usr/bin/env bash
# `streamlab hdr-verify` evaluator tests on generated HEVC samples; needs
# ffmpeg with libx265 and ffprobe. usage: tests/test-hdr.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bin=$here/../bin/streamlab
fail=0

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

check() { # name want got
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: want '$2' got '$3'"
    fail=1
  fi
}

gen() { # out pix_fmt x265-params
  ffmpeg -v error -f lavfi -i testsrc2=size=320x180:rate=30:duration=2 -pix_fmt "$2" \
    -c:v libx265 -preset ultrafast -x265-params "$3:log-level=error" -f hevc "$1"
}

gen "$work/hdr.h265" yuv420p10le 'hdr10=1:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400' \
  || { echo "FAIL generate hdr sample"; exit 1; }
gen "$work/sdr.h265" yuv420p 'repeat-headers=1:colorprim=bt709:transfer=bt709:colormatrix=bt709' \
  || { echo "FAIL generate sdr sample"; exit 1; }

gen "$work/sdr-novui.h265" yuv420p 'repeat-headers=1' \
  || { echo "FAIL generate sdr no-vui sample"; exit 1; }

# Prints the verdict word, rows' statuses and the exit code of one evaluation.
verdict() { # bin dump log [--expect-sdr]
  local b=$1 d=$2 l=$3 out rc
  shift 3
  out=$("$b" hdr-verify --dump "$d" --log "$l" --out-dir "$work/out" "$@" 2>&1)
  rc=$?
  printf '%s|%s|%s' "$(printf '%s\n' "$out" | head -n 1)" "$(printf '%s\n' "$out" | awk '/^  (PASS|FAIL)/ {printf "%s ", $1}')" "$rc"
  printf '%s\n' "$out" >"$work/last.out"
}

ok_log=$here/fixtures/hdr-client-ok.log
stock_log=$here/fixtures/hdr-client-stock.log

check hdr_sample "hdr-verify: PASS hdr 4/4|PASS PASS PASS PASS |0" "$(verdict "$bin" "$work/hdr.h265" "$ok_log")"
check sdr_sample_expect_sdr "hdr-verify: PASS sdr-control 1/1|PASS |0" "$(verdict "$bin" "$work/sdr.h265" "$ok_log" --expect-sdr)"
check sdr_novui_expect_sdr "hdr-verify: PASS sdr-control 1/1|PASS |0" "$(verdict "$bin" "$work/sdr-novui.h265" "$ok_log" --expect-sdr)"
check sdr_sample_as_hdr "hdr-verify: FAIL hdr 2/4|PASS FAIL FAIL PASS |1" "$(verdict "$bin" "$work/sdr.h265" "$ok_log")"
check hdr_sample_expect_sdr "hdr-verify: FAIL sdr-control 0/1|FAIL |1" "$(verdict "$bin" "$work/hdr.h265" "$ok_log" --expect-sdr)"
check stock_log_on_hdr_sample "hdr-verify: FAIL hdr 2/4|FAIL PASS PASS FAIL |1" "$(verdict "$bin" "$work/hdr.h265" "$stock_log")"
sed -n '2,$p' "$work/last.out"

# Mutation: flip one expected value in a copy of the driver; the matching
# row must go red while the unmutated rows stay green.
mutate() { # name sed-expr expected-rows
  local n
  n=$(grep -c -- "$2" "$bin" || true)
  check "mutant_${1}_target_unique" 1 "$n"
  sed "s/$2/$3/" "$bin" >"$work/mutant"
  chmod +x "$work/mutant"
  check "mutant_$1" "hdr-verify: FAIL hdr 3/4|$4|1" "$(verdict "$work/mutant" "$work/hdr.h265" "$ok_log")"
}
mutate c1 'want_scm=0x200' 'want_scm=0x400' 'FAIL PASS PASS PASS '
mutate c2 'transfer=smpte2084 primaries' 'transfer=arib-std-b67 primaries' 'PASS FAIL PASS PASS '
mutate c3 "sei_mastering='Mastering display metadata'" "sei_mastering='Mastering display metadatx'" 'PASS PASS FAIL PASS '
mutate c4 'want_enable=1' 'want_enable=0' 'PASS PASS PASS FAIL '

sed "s/want_sdr='profile=Main pix_fmt=yuv420p transfer=bt709'/want_sdr='profile=Main pix_fmt=yuv420p transfer=smpte2084'/" "$bin" >"$work/mutant-sdr"
cmp -s "$bin" "$work/mutant-sdr" && check mutant_c5_applied changed same
chmod +x "$work/mutant-sdr"
check mutant_c5 "hdr-verify: FAIL sdr-control 0/1|FAIL |1" "$(verdict "$work/mutant-sdr" "$work/sdr.h265" "$ok_log" --expect-sdr)"

exit "$fail"
