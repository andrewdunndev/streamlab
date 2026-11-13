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
second. A field the client did not print stays empty.

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

Does a game run through the stack a household uses? Per game, per vendor,
from a cold launch through the TV path: a Wolf UI stream, a lobby for the
Steam profile, and a `steam://rungameid` launch with no one at the controls.
`streamlab game run` records one row in `results/game.tsv`:

- Reaches the title screen: the game's app id becomes gamescope's focused
  app, and a screenshot shows the title.
- Focus: whether the game came to the front on its own. If Big Picture
  keeps focus while the game runs, `--baselayer` sets
  `GAMESCOPECTRL_BASELAYER_APPID` to the game's app id and the row says
  `forced=yes`. Record forced focus as a finding, not a pass.
- GPU busy: the maximum utilisation sampled while it ran, to tell a
  rendering game from a window that is mapped but idle.
- Time to first frame: seconds from launch until the game is focused,
  including the first-launch shader build. Samples are `--interval`
  apart, so the figure is that coarse.

`streamlab game diag` dumps why a running game has no focusable window.

## Tier 2: HDR

HDR is a later track. The first goal is a stream that works without
problems. HDR needs an HDR-capable server path, a client that sets the
connector's colorspace and metadata, and a display or plug whose EDID
advertises HDR. Whether the dummy plug advertises it is unverified. The track starts
once the baseline is stable, and its fixes go upstream.
