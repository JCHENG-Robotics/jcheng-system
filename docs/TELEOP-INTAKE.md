# Teleoperation intake (M2, mock)

> Status: **Experimental**, 2026-11-26. Mock/system core only. It does not
> run on hardware, does not use the vendor executor and does not create a
> second motion origin. The vendor executor cannot stream multi-joint motion;
> M6 remains blocked.

## 1. Purpose

`jcheng_system_core.teleop.TeleopIntake` is the system-side consumer for the
reserved teleoperation stream. It validates a high-rate `TeleopSample`, holds
the latest setpoint, and at a bounded period converts it into a multi-joint
`JOINT_STEP` that goes through the existing `MotionArbiter` lease, safety,
publisher guard and evidence path.

```text
TeleopCommand (domain 42, 50 Hz)
        │
        ▼
TeleopIntake.on_sample          contract, lease, seq, deadman, stamp, mode
        │
        ▼ 20 Hz intake / 10 Hz step
TeleopIntake.tick               measured state + velocity × dt
        │
        ▼
MotionArbiter.execute(JOINT_STEP, profile=pose)
        │  lease · SafetySupervisor · PublisherGuard · EvidenceStore
        ▼
mock hardware / future simulation bridge
```

## 2. M2 limits

- Only `JOINT_VELOCITY` is accepted.
- The step is a `JOINT_STEP` with profile `pose`; the mock supports
  multi-joint targets. The vendor executor accepts only one-joint `step`, so
  this path is **not** a hardware path.
- The step velocity is capped by the teleop intake (`0.5 rad/s` default) and
  the profile pose limit; the arbiter still validates start distance, step
  limit and bus verification.
- The intake is a test/system-core object. A ROS 2 Humble node, DDS QoS and
  the simulation bridge are not implemented on this branch yet.
- Deadman release and input timeout are **non-latching** teleop aborts; an
  explicit `TeleopIntake.stop()` calls `MotionArbiter.stop()` and latches.
- A lease is checked on every sample; no lease is rejected with the canonical
  `NO_LEASE` code.
- Sequence must strictly increase; a duplicate or rollback is rejected.
- The sample stamp must be within `max_stamp_skew_s` of arrival; liveness is
  based on the caller's arrival timestamp, not a cross-host wall clock.

## 3. Tests

`tests/test_teleop.py` covers:

- one step through the arbiter with bus verification;
- missing lease;
- non-increasing sequence;
- deadman release (no latch);
- input timeout (no latch);
- explicit stop (latches);
- unsupported Cartesian mode.

Run:

```bash
python3 -m pytest -q tests/test_teleop.py
bash scripts/check_repo_gate.sh
```

## 4. Open items for M2

1. A ROS 2 Humble subscriber for `jcheng_interfaces/msg/TeleopCommand` and a
   20 Hz timer on domain 42.
2. A simulation bridge that supplies measured state and accepts multi-joint
   targets on domain 43; it must remain test-only and must not make the
   simulation plant a motion authority.
3. A teleop-side DDS publisher with an HTTP lease adapter (or the operator
   console's external lease) and a `TeleopState` status surface.
4. QoS and timing characterization; the current tests use `ManualClock`.
5. A strict mode/purpose distinction in the future `TeleopCommand` v0.2.

## 5. Related

- [System architecture](ARCHITECTURE.md)
- [Safety](SAFETY.md)
- [Execution model](EXECUTION-MODEL-REVISION.md)
- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces)
- [`jcheng-teleop`](https://github.com/JCHENG-Robotics/jcheng-teleop)
- [`jcheng-simulation`](https://github.com/JCHENG-Robotics/jcheng-simulation)
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops)
- [`.github`](https://github.com/JCHENG-Robotics/.github)
