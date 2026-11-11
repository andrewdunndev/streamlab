#!/usr/bin/env bash
# Parser tests against fixture logs. usage: tests/test-parse.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bin=$here/../bin/streamlab
fail=0

# field LOGFILE COLUMN: value of the named column in the parse row
field() {
  "$bin" parse "$1" 2>/dev/null | awk -F'\t' -v c="$2" \
    'NR == 1 {for (i = 1; i <= NF; i++) if ($i == c) k = i} NR == 2 {print $k}'
}

check() { # name want got
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: want '$2' got '$3'"
    fail=1
  fi
}

f=$here/fixtures/stats.log
check res        1920x1080 "$(field "$f" WxH)"
check fps_in     59.98     "$(field "$f" fps_in)"
check fps_dec    59.97     "$(field "$f" fps_dec)"
check fps_render 59.95     "$(field "$f" fps_render)"
check host_lat   2.1       "$(field "$f" host_lat_avg)"
check net_drop   0.05      "$(field "$f" net_drop_pct)"
check jitter     0.10      "$(field "$f" jitter_drop_pct)"
check rtt        3         "$(field "$f" rtt)"
check decode     1.85      "$(field "$f" decode_ms)"
check queue      0.12      "$(field "$f" queue_ms)"
check render     8.33      "$(field "$f" render_ms)"
check codec      HEVC      "$(field "$f" codec)"

d=$(field "$f" decoder)
case $d in
  *"Renderer 'DrmRenderer' chosen"*"Chose VAAPIRenderer"*) echo "ok   decoder" ;;
  *) echo "FAIL decoder: got '$d'"; fail=1 ;;
esac

p=$here/fixtures/partial.log
check partial_host_lat '' "$(field "$p" host_lat_avg)"
check partial_rtt      '' "$(field "$p" rtt)"
check partial_drop     12.50 "$(field "$p" net_drop_pct)"

"$bin" parse "$here/fixtures/no-stats.log" >/dev/null 2>&1
check no_stats_exit 2 "$?"
check no_stats_res '' "$(field "$here/fixtures/no-stats.log" WxH)"

exit "$fail"
