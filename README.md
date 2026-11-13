![hero](hero.svg)

# streamlab

[![pipeline](https://gitlab.com/dunn.dev/streamlab/badges/main/pipeline.svg)](https://gitlab.com/dunn.dev/streamlab/-/pipelines)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![built with GitLab](https://img.shields.io/badge/built_with-GitLab-FC6D26?logo=gitlab)](https://gitlab.com/dunn.dev)

A reproducible test rig for [Wolf](https://github.com/games-on-whales/wolf) game streaming
with Moonlight clients.

We want game streaming that works without issues for a household, with HDR and upstream
contributions later. The rig measures regressions and settings before they reach anyone
playing. [METHOD.md](METHOD.md) has the method, including what it does not measure, so
others can reproduce it.

## The rig

Two hosts on the same network with the same CPU, one with an AMD GPU and one NVIDIA, each
running Wolf. An HDMI dummy plug on each discrete GPU lets either host act as a headless
Moonlight client for the other.

- `client/Containerfile`: a pinned Fedora image with moonlight-qt built from source against
  RPM Fusion ffmpeg. The NVIDIA userspace is injected at run time through CDI.
- `bin/streamlab`: a bash driver run from a workstation over ssh.
- `scripts/assert-client.sh`: build-time checks for the client image.
- `tests/`: parser tests against fixture logs, and `streamlab game` tests against stub hosts.
- `results/`: result TSVs, committed per baseline.

## Use

Hosts are arguments. The workstation needs ssh and passwordless sudo on both hosts, and
`jq` locally. Each host needs podman and a `wolf-idle-gate.sh` that exits non-zero while a
session is live.

```sh
bin/streamlab guard HOST_A HOST_B
bin/streamlab pair --server HOST_A --server-addr ADDR_A --client HOST_B
bin/streamlab run --server HOST_A --server-addr ADDR_A --client HOST_B \
  --app "Test ball" --codec HEVC --res 1920x1080 --fps 60 --bitrate 20000 \
  --secs 30 --runs 3 --out results/baseline.tsv
bin/streamlab parse results/logs/some-run.log
tests/test-parse.sh
tests/test-game.sh
```

### Per-game compatibility

`streamlab game` launches a Steam game through Wolf with no one at the controls: a
Wolf UI stream from the client host, a lobby for a Wolf profile that runs Steam, and a
`steam://rungameid` launch. The server logs focus, GPU load and time to first frame.
The Wolf host also needs `jq` and `curl`. Profile, app id, executable and Steam root
are flags.

```sh
bin/streamlab game run --server HOST_A --server-addr ADDR_A --client HOST_B \
  --profile PROFILE --appid APPID --exe GAME.exe --steam-root STEAM_DIR \
  --secs 300 --baselayer
```

`run` holds a stream, starts the lobby, probes for `--secs` seconds, appends a row to
`results/game.tsv`, then closes the game and stops the lobby. Each step is also a verb:

| Verb | Does |
|------|------|
| `lobby` | start PROFILE's first app in a lobby and join the live session; prints the lobby id. `--image IMG` swaps the runner image, `--env K=V` adds a variable. `--headless [--res WxH]` needs no Moonlight session, and the lobby stays up with no one in it |
| `hold` | start the Wolf UI stream in the background, then the lobby |
| `probe` | wait for Big Picture, launch `--appid`, log focus every `--interval` seconds |
| `diag` | launch `--appid`, wait for `--exe`, dump why it has no focusable window |
| `exec` | run `-- CMD` as the Steam user inside Steam's session environment |
| `close` | wait up to `--exe-wait` (600) seconds for `--exe`, let it run `--settle` (60) seconds, TERM it, wait for it to exit, then `--upload-wait` (30) seconds for the cloud upload |
| `stop` | close the game, stop `--lobby`, release the stream and client |

With `--baselayer`, when Big Picture holds focus and the game's window exists, the
probe sets `GAMESCOPECTRL_BASELAYER_APPID` to the app id. `stop` and `run` refuse to
stop a lobby without `--exe`, so the game is closed before its lobby stops; `--force`
overrides. `run --headless` needs no `--client` or `--server-addr`. Run state
(`hold.pid`, the stream log) goes under `$STREAMLAB_STATE`, default
`$TMPDIR/streamlab-game`.

## Build and release

CI builds the client image with the `container-image` component from
[dunn.dev/pipeline](https://gitlab.com/dunn.dev/pipeline) and publishes
`registry.gitlab.com/dunn.dev/streamlab/client`. Merge requests that touch `client/` or
`scripts/` build the image without pushing.

## License

[MIT](LICENSE). moonlight-qt and the RPM Fusion codecs carry their own licenses.
