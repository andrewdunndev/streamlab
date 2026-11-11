# Method

streamlab tests Wolf game-streaming hosts end to end with a Moonlight client
on a second host. The goal is to find regressions and measure settings
before anyone plays on them.

## Rig

Two Linux hosts with the same CPU on the same network segment. They differ
in one thing: one has an AMD discrete GPU, the other an NVIDIA one. Each
runs Wolf, and each has an HDMI dummy plug on its discrete GPU. The plug
gives the client a real connector to scan out to, so either host can act as
a headless Moonlight client for the other, using the client image in
`client/`. Swapping the roles gives the vendor A/B with everything else held
constant.

Hosts are passed to `bin/streamlab` as arguments. Nothing in the repo names
one.

## Guard and revert rules

- Never touch a host with a live session. `streamlab guard` asks each host's
  idle gate before every mutating step and again after every run. Any
  non-zero answer stops the run.
- Variants are runtime-only. A variant changes a setting for the length of a
  test and removes itself afterwards, including when the controlling
  process dies. Nothing persists across a reboot.
- A winning variant reaches a host only through the normal configuration
  review. The rig never promotes anything.

## What is measured

Each run produces one TSV row from the "Global video stats" block that
moonlight-qt prints at teardown: incoming, decoded and rendered frame rates,
host processing latency, network and jitter drops, round-trip time, and
average decode, queue and render time. The row also records which decoder and
renderer the client chose, and the average load of the server's GPU sampled
once a second during the run. A field the client did not print stays empty.

## What is not measured

Decode happens on the client host's GPU, not on a television or a phone.
Absolute numbers are a proxy. Trust the difference between two variants on
the same rig, not the number itself. Keep one human calibration point: when a
variant wins, play the same configuration on a real client for a few minutes
and read its overlay once. If that disagrees with the rig, distrust the rig
on that axis.

Input-to-photon latency and image quality are out of scope.

## Test order

1. Harness proof. Stream Wolf's built-in "Test ball" app for 30 seconds and
   get a row with real values. Repeat at 5 Mbps as a negative control; the
   drop or decode fields must move. If they do not, the harness measures
   nothing.
2. Nightly-pull smoke. After pulling new images on a host, run the smoke
   stream. If it fails, roll the pull back before anything else.
3. Baseline matrix. Codec, resolution and frame rate on each vendor, three
   runs per cell, committed under `results/`.
4. Setting A/B. One host setting per variant against the baseline, three runs
   each.
5. Image A/B. Two images of the same component, same settings, same cells.

Steps 4 and 5 compare against a baseline taken the same day on the same
hosts.

## HDR

HDR is a later track. The first goal is a stream that works without
problems. HDR needs an HDR-capable server path, a client that sets the
connector's colorspace and metadata, and a display or plug whose EDID
advertises HDR. Whether the dummy plug advertises it is unverified. The track
starts once the baseline is stable, and any fix that comes out of it goes
upstream.
