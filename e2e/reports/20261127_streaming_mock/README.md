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

`python3 -m pytest -q tests/test_streaming_mock.py` — **5 passed**.
`tests/test_stream_pack.py` — **10 passed** (atomic pulse packing: exact arm
coverage, mapping order, rad→pulse, missing/extra/non-finite/out-of-range and
duration bounds). Full core suite: **73 passed**; ROS suite: 15 passed
(1 skipped); gate OK.

## Vendor pack path

`jcheng_hardware/streaming.py` (`pack_arm_stream`) and
`VendorBridge.send_stream` are implemented: all arm joints are converted to
pulses in mapping order and published in **one** `ServosPosition` frame
(duration 0.02–0.5 s). No lease/deadman check is done in the bridge; that stays
in the system supervisor/arbiter.

## Arbiter streaming session

`MotionArbiter.stream(command)` publishes additional frames into a session
opened by `start` and closed by `finish`/`stop`:

- every frame re-validates lease, stop latch, limits and rate;
- a rejected frame aborts the session with its terminal result;
- lease loss latches (stream aborts `LEASE_REVOKED`);
- a stop-latched session accepts no further frame;
- `finish` bus-reads and completes once; frames are not terminal results.

`tests/test_streaming_arbiter.py` — **5 passed**: six-frame session then
COMPLETED with bus verification; lease revoke aborts and latches; stop prevents
frames; an out-of-limit frame aborts `JOINT_LIMIT`; no active session is
`MOTION_BUSY`.

## Limits

- Mock and pure packing only. The vendor `send_stream` path is not exercised
  against the vendor stack; a supervisor that publishes at the command period
  and performs a bus read is still missing.
- Vendor rate/interpolation remain unmeasured; a write session needs separate
  per-session authorization.
- The mock applies targets immediately; it does not model servo lag.
- No hardware, no vendor topic, no device.
