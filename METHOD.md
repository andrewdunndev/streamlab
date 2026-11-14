# Method

streamlab tests Wolf game-streaming hosts end to end with a Moonlight client
on a second host. We want to find regressions and measure settings before
anyone plays on them.

## Rig

Two Linux hosts with the same CPU on the same network segment. One has an
AMD discrete GPU, the other NVIDIA. Each runs Wolf and has an HDMI
dummy plug on its discrete GPU. The plug gives the client a real connector
to scan out to, so either host can act as a headless Moonlight client for
the other, using the image in `client/`. Swapping roles gives the vendor
A/B with everything else constant.

Hosts are arguments to `bin/streamlab`. Nothing in the repo names one.

## Guard and revert rules

- Never touch a host with a live session. `streamlab guard` asks each
  host's idle gate before every mutating step and again after every run. Any
  non-zero answer stops the run.
- A variant is runtime-only: it changes a setting for one
  test and removes itself afterwards, including when the controlling
  process dies. Nothing persists across a reboot.
- A winning variant reaches a host only through the normal configuration
  review. The rig never promotes anything.
- Close a game cleanly before its lobby stops. Killing it with the lobby
  leaves a cloud-save session pending, and the next launch on another host
  stalls on it. `streamlab game stop` refuses to stop a lobby unless it is
  given the game's executable to close first (or `--force`).

## What is measured

Each run produces one TSV row from the "Global video stats" block that
moonlight-qt prints at teardown: incoming, decoded and rendered frame rates,
host processing latency, network and jitter drops, round-trip time, and
average decode, queue and render time. The row also records which decoder and
renderer the client chose, and the server's average GPU load, sampled once a
second. A field the client did not print stays empty. `failed=` lists the decoders
that did not initialize before the one in `impl=` was chosen: on an NVIDIA client
`failed=VAAPI,VDPAU` is expected, and `impl=vulkan` or `impl=hevc_cuvid` is hardware
decode.

## What is not measured

Decode happens on the client host's GPU, not on a television or a phone, so
absolute numbers are a proxy. We trust the difference between two variants
on the same rig, not the number itself. We keep one human calibration point:
when a variant wins, we play the same configuration on a real client for a
few minutes and read its overlay once. If that disagrees with the rig, we
distrust the rig on that axis.

Input-to-photon latency and image quality are out of scope.

## Test order

1. Harness proof. Stream Wolf's built-in "Test ball" app for 30 seconds and
   get a row with real values. Repeat at 30 fps as a negative control: the
   frame-rate fields must halve, or the harness measures nothing. A 5 Mbps
   run is not a control here, since a test pattern streams cleanly at that
   rate and nothing moves.
2. Nightly-pull smoke. After pulling new images on a host, run the smoke
   stream. If it fails, roll the pull back before anything else.
3. Baseline matrix. Codec, resolution and frame rate on each vendor, three
   runs per cell, committed under `results/`. Codecs are the ones the
   server's GStreamer registers a hardware encoder for; a software-only
   codec is not a cell.
4. Setting A/B. One host setting per variant against the baseline, three runs
   each.
5. Image A/B. Two images of the same component, same settings, same cells.

Steps 4 and 5 compare against a same-day baseline on the same
hosts.

## Tier 1: per-game compatibility

We want to know whether a game starts with no one at the controls, per game
and per vendor. `streamlab game run` holds a Wolf UI stream, starts a lobby
for the Steam profile, launches the game with `steam://rungameid` and
samples it every `--interval` seconds. It appends one row to
`results/game.tsv` and saves a screenshot under `results/logs/`:

- `focused`: whether the game's app id is gamescope's focused app at the
  last sample. The screenshot shows what was on screen then.
- `forced`: whether `--baselayer` had to bring the game to the front. With
  that flag, if Big Picture keeps focus while the game's window exists, the
  probe sets `GAMESCOPECTRL_BASELAYER_APPID` to the game's app id. A
  `forced=yes` row is a finding: the game did not take focus on its own.
- `gpu_max`: the highest GPU utilisation sampled. A low figure means the
  window is mapped but the game is not rendering.
- `ttff_s`: seconds from launch until the game first has focus, including
  the first-launch shader build. It is a multiple of `--interval`.

`streamlab game diag` launches a game and dumps why it has no focusable
window.

## Tier 2: HDR

HDR is a later track. The first goal is a stream that works without
problems. HDR needs an HDR-capable server path, a client that sets the
connector's colorspace and metadata, and a display or plug whose EDID
advertises HDR. Whether the dummy plug advertises it is unverified. The track starts
once the baseline is stable, and its fixes go upstream.
