# PiPER X read-only run (P2) — 2026-11-28

> Status: **Partially verified** (read-only only). 30 min of passive CAN on both
> fixed-cell channels: `tx_packets=0` on both interfaces for the whole window,
> no bus errors, and the decoded joints match `piper_sdk` 0.6.2 byte for byte.
> No enable, no motion command, no DDS, no camera. This is not a P3 claim.

Platform decision: `jcheng-design` `docs/ecosystem/17-platform-pivot.md` and
ADR-0007 (fixed cell, PiPER X production arm, leader read-only).
Backend: [`jcheng_vendor.piper_readonly`](../../ros2_ws/src/jcheng_vendor/jcheng_vendor/piper_readonly.py).
Deployment profile: [`deploy/piper-cell/`](../../deploy/piper-cell/README.md).

## 1. Environment

| Item | Value |
|---|---|
| Host | AGX Orin, `192.168.10.101` |
| OS / kernel | Ubuntu 24.04.5 LTS, `6.8.12-1029-tegra` |
| Image | JetPack 7.2.2 (L4T 39.2.2), NVMe, flashed 2026-11-28 |
| CAN driver | in-kernel `gs_usb` (no userspace probe) |
| Adapters | 2 × bytewerk candleLight `1d50:606f` |
| Interfaces | `can_leader1`, `can_follower1` (udev by serial) |
| Bitrate | 1 000 000, sample-point 0.750, state `ERROR-ACTIVE` |
| Window | 2026-11-28T12:01:04Z → 12:31:04Z (20:01–20:31 +08) |
| Duration | 1802.515 s |

Channel topology, confirmed by the owner and by a hand-move test (moving the
right arm changed only `can_leader1`; `can_follower1` stayed byte-identical):

| Arm | Role | Serial | USB path | Interface |
|---|---|---|---|---|
| Right | leader, teach input only | `002900324148570B20343133` | `1-4.4.3` | `can_leader1` |
| Left | follower, motion output | `0042001F4648571220363032` | `1-4.4.4` | `can_follower1` |

## 2. Method

- `candump -t d can_leader1,2A1:7FF,2A5:7FF,2A6:7FF,2A7:7FF,2A8:7FF can_follower1,<same set>`
  for 1800 s. `-t d` logs the delta to the previous message, so the clock is
  accumulated globally.
- `tx_packets` sampled every 5 s into `tx_monitor.txt`; start counters in
  `counters_before.txt`.
- Independent big-endian decoding (`piper_readonly`) compared against
  `piper_sdk` 0.6.2 `C_PiperParserV2` on 43 878 sampled frames.
- `p2_1hz.jsonl.gz` is a 1 Hz downsample (joints, status, gripper) for
  inspection; the raw log stays on the Orin.

## 3. Result

```text
total_duration=1802.515s
can_follower1: frames=1799965 rate=998.6 Hz max_gap=8.3ms   (5 IDs, 199.7 Hz each)
can_leader1:   frames=1750905 rate=971.4 Hz max_gap=8.9ms   (5 IDs, 194.3 Hz each)
sdk parity:    frames_checked=43878 mismatches=0 max_abs_diff_deg=0.000000000
writes:        can_leader1 tx=0, can_follower1 tx=0 (before and after)
tx monitor:    716 samples, unique values [0]
bus errors:    rx_errors=0 on both interfaces
```

Status frame `0x2A1` had exactly one value per channel for the whole window:

| Channel | Raw | `ctrl_mode` | `arm_status` | `mode_feed` | `err_code` |
|---|---|---|---|---|---|
| `can_follower1` | `01 00 01 02 00 00 00 00` | `0x01` CAN | `0x00` normal | `0x01` MOVE J | `0x0000` |
| `can_leader1` | `02 00 00 01 00 00 00 00` | `0x02` teaching | `0x00` normal | `0x00` MOVE P | `0x0000` |

Joint ranges (degrees, vendor feedback zero):

| Channel | j1 | j2 | j3 | j4 | j5 | j6 |
|---|---:|---:|---:|---:|---:|---:|
| follower min..max | -7.316 | 2.791 | 0.000 | -6.769 | 9.166 | -11.993 |
| follower span | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| leader min..max | -5.916 | 2.789..2.891 | 2.496 | -4.644..1.488 | -12.662..3.605 | 8.930..45.049 |

The follower held position with zero span. The leader is the hand-movable
teach arm and moved during the window (j6 span 36.1 deg), so its range is not a
stability measurement.

Gripper `0x2A8`: follower angle 0.100 deg constant, effort -106..-12; leader
angle -3.0..94.5 deg (hand motion), effort -1228..181.

## 4. Finding: vendor zero is not the URDF zero

The leader read `j2=+2.8 deg`, `j3=+2.5 deg`, while the simulation profile
expects `j2 0..180 deg` and `j3 -170..0 deg`. The feedback zero is the vendor
controller zero, not the URDF zero. The v2 profile limits stay simulation-only
and must not be enforced on hardware feedback until a per-unit calibration
record exists (risk R38 in the pivot record).

## 5. Limits

- Read-only: no enable, torque, motion or parameter write was sent. The leader
  was never commanded; the follower was already holding position before the
  window.
- `rx_dropped` reads 10 771 / 11 178 since the interfaces came up at ~11:58,
  which includes an earlier 120 s hand-move capture at ~3 200 frames/s. Inside
  the 30-min window the per-ID rate is stable (199.7 / 194.3 Hz) and the max
  per-channel gap is below 9 ms; `rx_errors=0` throughout. The drop counter is
  a host socket-buffer statistic, not a bus fault.
- No cameras, no DDS, no ROS nodes, no MoveIt, no AR5. P2 exit is
  `Partially verified`; only a P3 supervised pick-and-place can be `Verified`.

## 6. Artifacts

| File | Content |
|---|---|
| `summary.txt` | full analysis output |
| `p2_1hz.jsonl.gz` | 1 Hz decoded samples, both channels |
| `tx_monitor.txt` | `tx_packets` every 5 s, all zero |
| `counters_before.txt` | start-of-window counters |
| `frames_sample.txt` | first and last 20 raw frames |
| `SHA256SUMS` | hashes of the files above |

Raw capture: `/tmp/p2_30min.log` on the Orin, 230 806 550 bytes,
`sha256:5b5c66b4833ee566bc2dd93117fa2a334f67a09df31d4c7093b2386522d673c6`.
It is not committed; the commands in §2 regenerate it.
