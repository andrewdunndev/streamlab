#!/usr/bin/env bash
# `streamlab game` tests against stub ssh, sudo, podman and Steam tools.
# usage: tests/test-game.sh
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
bin=$here/../bin/streamlab
fail=0

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
stubs=$work/stubs
mkdir -p "$stubs"

check() { # name want got
  if [ "$2" = "$3" ]; then
    echo "ok   $1"
  else
    echo "FAIL $1: want '$2' got '$3'"
    fail=1
  fi
}

# ssh: logs "verb args" per remote call; FAKE_MODE=live also runs the call
# locally, with the stubs below standing in for podman, sudo and Steam.
cat >"$stubs/ssh" <<'EOF'
#!/bin/sh
cmd=$(for a; do :; done; printf '%s' "$a")
case $cmd in
  "sh -s -- "*) call=${cmd#sh -s -- } ;;
  *) call=$cmd ;;
esac
case $cmd in "sh -s -- "*) cat >"$FAKE_DIR/stdin" ;; esac
printf '%s\n' "$call" >>"$FAKE_DIR/$([ "$call" = "$cmd" ] && echo other || echo calls)"
verb=${call%% *}
rc=0
eval "rc=\${FAKE_RC_$verb:-0}" 2>/dev/null
[ "$rc" = 0 ] || exit "$rc"
case $verb in
  lobby) [ "${FAKE_MODE:-log}" = live ] || { echo lobby-123; exit 0; } ;;
  sessions) [ "${FAKE_MODE:-log}" = live ] || { echo 1; exit 0; } ;;
  status) [ "${FAKE_MODE:-log}" = live ] || { echo "WolfSteam_x sessions=0"; exit 0; } ;;
esac
[ "${FAKE_MODE:-log}" = live ] && exec sh -c "$cmd" <"$FAKE_DIR/stdin"
exit 0
EOF
cat >"$stubs/sleep" <<'EOF'
#!/bin/sh
echo "$1" >>"$FAKE_DIR/sleeps"
[ -e "$FAKE_DIR/start_on_sleep" ] && : >"$FAKE_DIR/running"
exit 0
EOF
cat >"$stubs/sudo" <<'EOF'
#!/bin/sh
[ "$1" = -n ] && shift
exec "$@"
EOF
cat >"$stubs/podman" <<'EOF'
#!/bin/sh
case $1 in
  ps) echo WolfSteam_test ;;
  exec) shift; [ "$1" = -u ] && shift 2; [ "$1" = -i ] && shift; shift; exec "$@" ;;
  cp) : >"$3" ;;
esac
EOF
cat >"$stubs/pgrep" <<'EOF'
#!/bin/sh
for a; do last=$a; done
case $last in
  steam | steamwebhelper) echo 123 ;;
  *) [ -e "$FAKE_DIR/running" ] && echo 999 || exit 1 ;;
esac
EOF
cat >"$stubs/pkill" <<'EOF'
#!/bin/sh
echo "$*" >>"$FAKE_DIR/signals"
rm -f "$FAKE_DIR/running"
EOF
cat >"$stubs/steam" <<'EOF'
#!/bin/sh
: >"$FAKE_DIR/running"
echo "$@" >>"$FAKE_DIR/launches"
EOF
cat >"$stubs/xprop" <<'EOF'
#!/bin/sh
case $* in
  *"-set GAMESCOPECTRL_BASELAYER_APPID "*)
    for a; do last=$a; done
    echo "$last" >"$FAKE_DIR/focused"
    echo "set $last" >>"$FAKE_DIR/baselayer"
    ;;
  *GAMESCOPE_FOCUSED_APP) echo "GAMESCOPE_FOCUSED_APP(CARDINAL) = $(cat "$FAKE_DIR/focused")" ;;
  *GAMESCOPECTRL_BASELAYER_APPID) echo "GAMESCOPECTRL_BASELAYER_APPID(CARDINAL) = 769" ;;
  *GAMESCOPE_FOCUSABLE_WINDOWS)
    if [ -e "$FAKE_DIR/running" ]; then
      echo "GAMESCOPE_FOCUSABLE_WINDOWS(CARDINAL) = 1, 769, 100, 2, $GAME_APPID, 200"
    else
      echo "GAMESCOPE_FOCUSABLE_WINDOWS(CARDINAL) = 1, 769, 100"
    fi
    ;;
esac
EOF
cat >"$stubs/gamescopectl" <<'EOF'
#!/bin/sh
exit 0
EOF
cat >"$stubs/nvidia-smi" <<'EOF'
#!/bin/sh
echo 41
EOF
chmod +x "$stubs"/*

reset() {
  rm -rf "$work/fake"
  mkdir -p "$work/fake"
  : >"$work/fake/calls"
  echo 769 >"$work/fake/focused"
}

# game ARGS...: runs the driver with the stubs first on PATH
game() {
  FAKE_DIR=$work/fake GAME_APPID=1465360 TMPDIR=$work STREAMLAB_STATE=$work/state \
    PATH=$stubs:$PATH "$bin" game "$@"
}

verbs() { awk '{print $1}' "$work/fake/calls" | tr '\n' ' ' | sed 's/ $//'; }

# usage and option errors never reach a host
reset
game >/dev/null 2>&1
check usage_exit 2 "$?"
game lobby --server s >/dev/null 2>&1
check lobby_needs_profile 1 "$?"
game probe --appid 1 --bogus x >/dev/null 2>&1
check unknown_option 1 "$?"
check no_remote_calls '' "$(verbs)"

# the close-before-stop rule
reset
game stop --server s --server-addr a --client c --lobby L >/dev/null 2>&1
check stop_needs_exe_or_force 1 "$?"
check stop_refused_touches_nothing '' "$(verbs)"

reset
game stop --server s --server-addr a --client c --lobby L --exe Game.exe >/dev/null 2>&1
check stop_with_exe 0 "$?"
check close_precedes_stop 'close stop status' "$(verbs)"

reset
FAKE_RC_close=1 game stop --server s --server-addr a --client c --lobby L --exe Game.exe >/dev/null 2>&1
check failed_close_keeps_lobby 1 "$?"
check failed_close_no_stop 'close' "$(verbs)"

reset
game stop --server s --server-addr a --client c --lobby L --force >/dev/null 2>&1
check force_skips_close 'stop status' "$(verbs)"

reset
game run --server s --server-addr a --client c --profile p --appid 1 >/dev/null 2>&1
check run_needs_exe_or_force 1 "$?"

# the remote program parses as POSIX sh
reset
game lobby --server s --profile 'a b' --env 'K=v w' >"$work/out" 2>&1
check lobby_prints_id lobby-123 "$(cat "$work/out")"
check lobby_args_quoted "lobby 'a b' 'K=v w' '' '0' '1920x1080'" "$(cat "$work/fake/calls")"
sh -n "$work/fake/stdin"
check remote_syntax 0 "$?"
if command -v shellcheck >/dev/null; then
  shellcheck -s sh "$work/fake/stdin" >/dev/null 2>&1
  check remote_shellcheck 0 "$?"
fi

# probe: Big Picture holds focus; the game is only forced forward on request
field() { awk -v k="$1" '$1 == "result" {for (i = 2; i <= NF; i++) {split($i, kv, "="); if (kv[1] == k) print kv[2]}}' "$work/out"; }

reset
FAKE_MODE=live game probe --server s --appid 1465360 --secs 40 --interval 10 --tag t >"$work/out" 2>&1
check probe_ttff_none none "$(field ttff_s)"
check probe_focused_no no "$(field focused)"
check probe_forced_no no "$(field forced)"
check probe_gpu_max 41 "$(field gpu_max)"
check probe_launch 'steam://rungameid/1465360' "$(cat "$work/fake/launches")"
check probe_no_baselayer_write '' "$(cat "$work/fake/baselayer" 2>/dev/null)"
check probe_shot "shot $work/t.png" "$(grep '^shot ' "$work/out")"

reset
FAKE_MODE=live game probe --server s --appid 1465360 --secs 40 --interval 10 --tag t --baselayer >"$work/out" 2>&1
check baselayer_sets_appid 'set 1465360' "$(head -1 "$work/fake/baselayer" 2>/dev/null)"
check baselayer_ttff 20 "$(field ttff_s)"
check baselayer_focused yes "$(field focused)"
check baselayer_forced yes "$(field forced)"

# close: TERM, wait for exit, then the cloud log
reset
: >"$work/fake/running"
mkdir -p "$work/root/logs"
echo "1465360 upload done" >"$work/root/logs/cloud_log.txt"
FAKE_MODE=live game close --server s --exe Game.exe --appid 1465360 --steam-root "$work/root" >"$work/out" 2>&1
check close_exit 0 "$?"
check close_termed '-TERM -x -- Game.exe' "$(cat "$work/fake/signals")"
check close_cloud_log '1465360 upload done' "$(tail -1 "$work/out")"
check close_soaks_60s 60 "$(grep -x 60 "$work/fake/sleeps" | head -1)"

# close: a slow first launch is waited for, then soaked, then closed
reset
: >"$work/fake/start_on_sleep"
FAKE_MODE=live game close --server s --exe Game.exe --appid 1465360 >"$work/out" 2>&1
check close_waits_for_exe 0 "$?"
check close_waited_then_termed 'Game.exe up after 10s' "$(grep 'up after' "$work/out")"
check close_slow_launch_termed '-TERM -x -- Game.exe' "$(cat "$work/fake/signals")"

reset
FAKE_MODE=live game close --server s --exe Game.exe --exe-wait 30 >"$work/out" 2>&1
check close_never_started_rc 0 "$?"
check close_never_started_msg 'Game.exe not running after 30s' "$(cat "$work/out")"
check close_never_started_no_term '' "$(cat "$work/fake/signals" 2>/dev/null)"

# the exe matcher is the process name, cut to the 15 characters comm keeps
eval "$(sed -n '/^exe_comm()/p' "$bin")"
comm=
exe_comm Game.exe
check exe_comm_short Game.exe "$comm"
exe_comm SnowRunner-Win64-Shipping.exe
check exe_comm_truncated SnowRunner-Win6 "$comm"

# option validation
reset
game probe --server s --appid 1 --interval 0 >/dev/null 2>&1
check interval_zero_refused 1 "$?"
game probe --server s --appid 1 --secs abc >/dev/null 2>&1
check secs_non_numeric_refused 1 "$?"
game exec --server s >"$work/out" 2>&1
check exec_needs_command_msg 'streamlab: game exec: a command after -- is required' "$(cat "$work/out")"
game probe --server s --appid 1 --tag '../x' >/dev/null 2>&1
check tag_path_refused 1 "$?"
check validation_touches_nothing '' "$(verbs)"

# lobby: the JSON body, joined or headless
cat >"$stubs/curl" <<'EOF'
#!/bin/sh
for a; do u=$a; done
case $u in
  */profiles) echo '{"profiles":[{"id":"p","apps":[{"runner":{"name":"steam","image":"orig","env":["A=1"]}}]}]}' ;;
  */sessions) echo '{"sessions":[{"client_id":"c1","client_settings":{"run_uid":1},"audio_channel_count":6,"video_width":1280,"video_height":720,"video_refresh_rate":60}]}' ;;
  */lobbies/create) cat >"$FAKE_DIR/create.json"; echo '{"lobby_id":"L1"}' ;;
  */lobbies/join) cat >"$FAKE_DIR/join.json" ;;
  */lobbies/stop) cat >"$FAKE_DIR/stop.json" ;;
esac
EOF
cat >"$stubs/podman" <<'EOF'
#!/bin/sh
case $1 in
  ps) echo WolfSteam_test; echo Wolf-UI_c1 ;;
  inspect)
    case $2 in
      wolf) echo '[{"Config":{"Env":[]}}]' ;;
      *) echo '[{"Config":{"Env":["WOLF_VIDEO_BUFFER_CAPS=video/x-raw(memory:CUDAMemory)"]}}]' ;;
    esac
    ;;
  exec) shift; [ "$1" = -u ] && shift 2; [ "$1" = -i ] && shift; shift; exec "$@" ;;
  cp) : >"$3" ;;
esac
EOF
chmod +x "$stubs"/*

if command -v jq >/dev/null; then
  reset
  FAKE_MODE=live game lobby --server s --profile p --env K=v --image img:2 >"$work/out" 2>&1
  check lobby_live_id L1 "$(cat "$work/out")"
  check lobby_runner_image img:2 "$(jq -r .runner.image "$work/fake/create.json")"
  check lobby_runner_env '["A=1","K=v"]' "$(jq -c .runner.env "$work/fake/create.json")"
  check lobby_buffer_caps 'video/x-raw(memory:CUDAMemory)' "$(jq -r .video_settings.video_producer_buffer_caps "$work/fake/create.json")"
  check lobby_render_node_default /dev/dri/renderD128 "$(jq -r .video_settings.runner_render_node "$work/fake/create.json")"
  check lobby_stops_when_empty true "$(jq -r .stop_when_everyone_leaves "$work/fake/create.json")"
  check lobby_joins_session c1 "$(jq -r .moonlight_session_id "$work/fake/join.json")"

  reset
  FAKE_MODE=live game lobby --server s --profile p --headless --res 2560x1440 >"$work/out" 2>&1
  check headless_id L1 "$(cat "$work/out")"
  check headless_size 2560x1440 "$(jq -r '"\(.video_settings.width)x\(.video_settings.height)"' "$work/fake/create.json")"
  check headless_persists false "$(jq -r .stop_when_everyone_leaves "$work/fake/create.json")"
  check headless_no_join no "$([ -e "$work/fake/join.json" ] && echo yes || echo no)"

  reset
  FAKE_MODE=live game stop --server s --lobby "L'1" --force >/dev/null 2>&1
  check stop_json_escaped "L'1" "$(jq -r .lobby_id "$work/fake/stop.json")"
fi

# headless run: no client, no stream, one probe, then close and stop
reset
game run --server s --profile p --appid 1465360 --exe Game.exe --headless \
  --results "$work/res/game.tsv" --shot-dir "$work/res/logs" >/dev/null 2>&1
check headless_run_rc 0 "$?"
check headless_run_verbs 'lobby probe close stop status' "$(verbs)"
check headless_run_lobby_args "lobby 'p' '' '' '1' '1920x1080'" "$(head -1 "$work/fake/calls")"

exit "$fail"
