# Mock streaming bridge (M6, offline)

> Status: **Partially verified (L2 mock, 2026-11-27)**. No vendor topic, no
> `/dev/rrc` write, no hardware and no motion.

## Implemented

- `jcheng_system_core/streaming.py`: `MockStreamingBridge` extends the mock
  hardware port and records one `StreamingFrame` per multi-joint
  `JOINT_STEP`/`HOME`: the full joint name list, targets and duration. A
  single-joint step is not logged as a streaming frame.
- `TeleopIntake` now anchors `last_tick_s` on the actual tick time instead of
  adding the step period: the old grid accumulated drift and skipped a step
  when the timer landed microseconds early (found by the 110 ms cadence test).

## Behaviour proven

Using the ArmPi profile and 10 intake ticks at a 110 ms cadence:

```text
10 atomic frames, each with all 5 joints and duration 0.1 s
targets 0.02, 0.02, -0.02, 0.02, -0.02 per step
last frame 0.2, 0.2, -0.2, 0.2, -0.2
no per-joint frame was emitted
```

Fault paths:

- deadman release: no further frame, no safety latch;
- lease revoke: the next sample is rejected (`LEASE_REVOKED`) before a frame;
- software stop latch: no further frame, `stop_latched` true;
- a one-joint `JOINT_STEP` completes but is not recorded as a stream frame.

## Tests

`python3 -m pytest -q tests/test_streaming_mock.py` — **5 passed**. Full core
suite: **58 passed**; ROS suite: 16 passed (2 skipped); gate OK.

## Limits

- Mock only. The real vendor path (`ServosPosition` on the vendor topic) still
  needs a `send_stream` on `VendorBridge` and a supervisor loop that publishes
  at the command period, then a bus read.
- Vendor rate/interpolation remain unmeasured; a write session needs separate
  per-session authorization.
- The mock applies targets immediately; it does not model servo lag.
- No hardware, no vendor topic, no device.
