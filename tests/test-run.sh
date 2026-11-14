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

# ssh: logs each remote command; a stream command prints a stats log on stderr.
cat >"$work/stubs/ssh" <<'EOS'
#!/bin/sh
for a; do :; done
printf '%s\n' "$a" >>"$FAKE_DIR/calls"
case $a in
  *" stream "*) cat "$FAKE_DIR/stats.log" >&2 ;;
  "sh -s") cat >/dev/null ;;
esac
exit 0
EOS
cat >"$work/stubs/sleep" <<'EOS'
#!/bin/sh
echo "$1" >>"$FAKE_DIR/sleeps"
EOS
chmod +x "$work/stubs"/*
cp "$here/fixtures/stats.log" "$work/stats.log"

run() { # ARGS...: runs the driver in a scratch directory
  (cd "$work" && FAKE_DIR=$work PATH=$work/stubs:$PATH "$bin" run \
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

run --secs 1 --resumes 0 --out bad.tsv
check bad_resumes_rc 1 "$?"

exit "$fail"
