# Streaming runtime (M6, domain 42, mock)

> Status: **Partially verified (L3 mock runtime, 2026-11-27)**. No vendor topic,
> no `/dev/rrc` write, no hardware and no motion.

## Run

```bash
# runtime on domain 42, mock hardware
ROS_DOMAIN_ID=42 JCHENG_ROOT=$PWD ros2 run jcheng_runtime jcheng_runtime \
  --ros-args -p robot:=armpi_ultra -p streaming:=true

# probe on the same domain
ROS_DOMAIN_ID=42 python3 tools/teleop_stream_probe.py \
  --joints 5 --velocity 0.3,0.3,-0.3,0.3,-0.3 --duration-s 2.0
```

`streaming:=true` constructs `TeleopIntake(streaming=True,
step_period_s=0.05)`; the runtime's 20 Hz timer calls `arbiter.start` once,
`arbiter.stream` per frame, and `finish` on the neutral/deadman frame.

## Result

```json
{"frames": 100, "first": [0.0, 0.0, 0.0, 0.0, 0.0],
 "last": [0.33, 0.33, -0.33, 0.33, -0.33],
 "delta": [0.33, 0.33, -0.33, 0.33, -0.33], "moved_joints": 5}
```

All five joints moved ±0.33 rad in one streaming session (`0.3 rad/s × 0.05 s`
per frame, about 22 intake frames over 2 s). The neutral frame closed the
session; the runtime log has no step warning, no abort and no latch. The probe
released the lease.

## Finding

`TeleopIntake.last_seq` persists across probe sessions in one runtime process;
a second probe that restarts `seq` at 0 is rejected with
`teleop seq must strictly increase`. The evidence run used a fresh runtime.
A future probe should accept a starting sequence or the runtime should scope
sequence numbers per session/lease.

## Tests

Core suite: **76 passed**; gate OK. This report is the L3 integration evidence
for the streaming intake on the mock stack.

## Limits

- Mock hardware only; the domain-43 simulation bridge and the vendor
  `ServosPosition` path are not exercised.
- No latency/tracking distribution; only endpoint deltas.
- The 20 Hz runtime timer produced ~22 frames over 2 s (about 11 Hz under this
  host load); the intake is bounded by the timer, not a real-time guarantee.
- No hardware, no vendor write, no device.
