#!/usr/bin/env bash
# `streamlab run` tests against a stub ssh. usage: tests/test-run.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bin=$here/../bin/streamlab
fail=0

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/stubs"

check() { # name want got
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: want '$2' got '$3'"
    fail=1
  fi
}

# ssh: logs each remote command; a stream command prints a stats log on stderr
# and, when FAKE_HANG is set, stays up until a podman stop arrives.
cat >"$work/stubs/ssh" <<'EOS'
#!/bin/sh
for a; do :; done
printf '%s\n' "$a" >>"$FAKE_DIR/calls"
case $a in
  *" stream "*)
    rm -f "$FAKE_DIR/stopped"
    echo "00:00:01 - SDL Info (0): Starting video stream..." >&2
    cat "$FAKE_DIR/stats.log" >&2
    if [ -n "${FAKE_HANG:-}" ]; then
      while [ ! -e "$FAKE_DIR/stopped" ]; do /bin/sleep 0.1; done
    fi ;;
  *"podman stop"*) : >"$FAKE_DIR/stopped" ;;
  "sh -s") cat >/dev/null ;;
esac
exit 0
EOS
cat >"$work/stubs/sleep" <<'EOS'
#!/bin/sh
[ "$1" = 0.5 ] && exec /bin/sleep 0.05
echo "$1" >>"$FAKE_DIR/sleeps"
EOS
chmod +x "$work/stubs"/*
cp "$here/fixtures/stats.log" "$work/stats.log"

run() { # ARGS...: runs the driver in a scratch directory
  (cd "$work" && FAKE_DIR=$work FAKE_HANG=${FAKE_HANG:-} PATH=$work/stubs:$PATH "$bin" run \
    --server s --server-addr 10.0.0.1 --client c --app App "$@" >/dev/null 2>&1)
}
count() { grep -c -- "$1" "$work/calls" 2>/dev/null || true; }
runcol() { awk -F'\t' 'NR > 1 {printf "%s ", $5}' "$1"; }

run --secs 1 --runs 1 --out plain.tsv
check plain_rc 0 "$?"
check plain_streams 1 "$(count ' stream ')"
check plain_quits 1 "$(count ' quit ')"
check plain_runs '1 ' "$(runcol "$work/plain.tsv")"

: >"$work/calls"
: >"$work/sleeps"
run --secs 1 --runs 2 --resumes 3 --gap 7 --out resume.tsv
check resume_rc 0 "$?"
check resume_streams 6 "$(count ' stream ')"
check resume_quits 2 "$(count ' quit ')"
check resume_runs '1.1 1.2 1.3 2.1 2.2 2.3 ' "$(runcol "$work/resume.tsv")"
check resume_gaps 4 "$(grep -c '^7$' "$work/sleeps")"
check resume_cols 1 "$(awk -F'\t' '{print NF}' "$work/resume.tsv" | sort -u | wc -l | tr -d ' ')"


col() { # file name: values of the named column, space separated
  awk -F'\t' -v c="$2" 'NR == 1 {for (i = 1; i <= NF; i++) if ($i == c) k = i} NR > 1 {printf "%s ", $k}' "$1"
}
check server_ended 'server ' "$(col "$work/plain.tsv" ended)"

: >"$work/calls"
FAKE_HANG=1 run --secs 2 --runs 1 --resumes 2 --gap 0 --out hang.tsv
check hang_rc 0 "$?"
check hang_ended 'timer timer ' "$(col "$work/hang.tsv" ended)"
check hang_stops 2 "$(count 'podman stop')"
secs=$(col "$work/hang.tsv" stream_s | awk '{print ($1 >= 2 && $2 >= 2) ? "ok" : "short"}')
check hang_duration ok "$secs"

run --secs 1 --resumes 0 --out bad.tsv
check bad_resumes_rc 1 "$?"

vout=$(cd "$work" && FAKE_DIR=$work PATH=$work/stubs:$PATH "$bin" run --server s --server-addr 10.0.0.1 \
  --client c --app App --secs 1 --runs 1 --verdict --out v.tsv 2>"$work/err.txt")
check verdict_rc 0 "$?"
check verdict_pass 'VERDICT PASS' "$(printf '%s' "$vout" | grep '^VERDICT' | cut -d' ' -f1-2)"

cp "$work/stats.log" "$work/stats.good"
: >"$work/stats.log"
vout=$(cd "$work" && FAKE_DIR=$work PATH=$work/stubs:$PATH "$bin" run --server s --server-addr 10.0.0.1 \
  --client c --app App --secs 1 --runs 1 --verdict --out v2.tsv 2>"$work/err.txt")
check verdict_fail_rc 1 "$?"
check verdict_fail 'VERDICT FAIL' "$(printf '%s' "$vout" | grep '^VERDICT' | cut -d' ' -f1-2)"
cp "$work/stats.good" "$work/stats.log"

vout=$(cd "$work" && FAKE_DIR=$work FAKE_HANG=1 PATH=$work/stubs:$PATH "$bin" run --server s --server-addr 10.0.0.1 \
  --client c --app App --secs 60 --runs 1 --max-seconds 5 --verdict --out v3.tsv 2>"$work/err.txt")
check watchdog_rc 124 "$?"
check watchdog_hang 'VERDICT HANG' "$(printf '%s' "$vout" | grep '^VERDICT' | cut -d' ' -f1-2)"

run --secs 1 --max-seconds 0 --out bad2.tsv
check bad_maxsecs_rc 1 "$?"

check verdict_fields 'fps= decoder=' "$(printf '%s' "$vout" | grep -o 'fps=\|decoder=' | tr '\n' ' ' | sed 's/ $//')"
check verdict_no_old_fields 0 "$(printf '%s' "$vout" | grep -c 'frames=\|encoder=')"

# A finished run must not leave the watchdog's sleep holding a pipe open.
mkdir -p "$work/real"
cp "$work/stubs/ssh" "$work/real/ssh"
t0=$SECONDS
(cd "$work" && FAKE_DIR=$work PATH=$work/real:$PATH "$bin" run --server s --server-addr 10.0.0.1 \
  --client c --app App --secs 1 --runs 1 --max-seconds 40 --verdict --out v4.tsv 2>/dev/null | cat >/dev/null)
check watchdog_pipe_released ok "$([ $((SECONDS - t0)) -lt 20 ] && echo ok || echo held)"

exit "$fail"
