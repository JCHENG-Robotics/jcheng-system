# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `jcheng_system_core.teleop.TeleopIntake`: mock/system-side teleoperation
  intake. It validates contract, lease, sequence, deadman, stamp and mode,
  holds the latest setpoint, and converts it at a bounded period into a
  multi-joint `JOINT_STEP` through the existing `MotionArbiter` lease, safety,
  publisher guard and evidence path. Deadman/timeout are non-latching aborts;
  explicit stop latches. Mock only; no vendor or hardware path.
- `tests/test_teleop.py`: seven L2 tests for step, missing lease, sequence,
  deadman release, timeout, explicit stop and unsupported Cartesian mode.
- `jcheng_runtime` teleop subscriber: `/jcheng/intent/teleop` (best-effort,
  depth 1) converts `TeleopCommand` to `TeleopSample`; the 20 Hz runtime timer
  drives the intake. `tests_ros/test_teleop_runtime.py` adds three ROS tests.
- `docs/TELEOP-INTAKE.md`: M2 scope, limits and open items.

- `tools/concurrency_probe.py`: P0 probes C1–C5 plus a stop/execute stress
  loop against the real mock gateway application and `MockStack`, with a
  harness-only settle window. Loopback only; no hardware.
- `e2e/reports/20261126_concurrency_probe/`: raw probe report and findings.
  C2 fails: one intent produced both `ABORTED` and `COMPLETED`, and the cached
  result was overwritten to `COMPLETED`; lease revoke/expiry during a motion
  was not enforced.
- `docs/EXECUTION-MODEL-REVISION.md`: proposed single-writer session state
  machine, once-only terminal result, stop path, tick thread and stop
  confirmation levels. Proposal only; no code or contract change.
- `e2e/reports/20261126_concurrency_probe_r2/`: re-run evidence after R1–R4.
  C1–C5, C5b and the 30-iteration stop/execute stress loop pass; C2 produces
  one `ABORTED` terminal and lease loss produces `LEASE_REVOKED`/`LEASE_EXPIRED`.
- `TickThread` in the gateway process: 20 Hz `sync_state` plus `arbiter.tick`.
- Read-only WebSocket stream at `GET /v1/ws/state` on both the mock and
  read-only gateways. The stdlib HTTP handlers upgrade to RFC 6455 with Bearer
  authentication and send one `hello` then `state`/`safety` envelopes at about
  10 Hz (`jcheng-ws.v1.json`). Ping/pong and close are handled; there is no
  lease, motion, event or write path. No new runtime dependency; six tests
  cover both gateways, auth, upgrade rejection and ping.
- `scripts/humble_ci.sh`: local Humble build/test wrapper (m3, m4, m5-mock,
  m5-vendor, m6) in the `armpi-humble-moveit` image, with a disposable
  workspace copy. CI remains the artifact authority.
- `deploy/armpi-ultra/`: runtime spec, flatten/publish scripts, image lock and
  the 2026-11-25 acceptance. Published
  `ghcr.io/jcheng-robotics/armpi-ultra:20261125` (`linux/arm64`, 7.1 GB,
  digest `sha256:d4e6f3901f1c43e08f4018ff7f7b55dec21b2955dba8b98404a440a1f89df15f`);
  20 ROS nodes / 60 topics were unchanged and the container survived a host
  reboot.
- `jcheng_hardware/readonly.py`: a read-only bus bridge with strictly
  increasing servo IDs, 0–1000 pulse validation and no publish/write API.
- `jcheng_telemetry`: telemetry cache with a `measured`/`commanded`
  distinction, read-only safety view, read-only HTTP gateway, preflight,
  evidence and a short mock probe.
- `readonly.launch.py`: starts only the telemetry node; no vendor container or
  motion node.
- Domain 0/41/42/43 configuration, CycloneDDS unicast templates and read-only
  allow/deny lists.
- `scripts/check_m3_gate.sh` (frozen M3 package set) and
  `scripts/check_m4_readonly.sh`; CI job `m4-readonly`.
- `jcheng_runtime`: M5.1 ROS service/action transport with lease, safety,
  validation and stop latch; uses `MockStack` only.
- `tests_ros` (5 tests) and `scripts/check_m5_mock.sh`; CI job `m5-mock`.
- `jcheng_vendor` and `motion_vendor.launch.py`: the single `/servo_controller`
  publisher, single-joint/gripper execution, `GetBusServoState` verification and
  stop latch/hold.
- `jcheng_moveit` plan-only goal construction/validation (M6 S17), rejecting
  execute/trajectory requests; verified against real MoveIt 2.5.10 on
  2026-11-25 (`SUCCESS`, 8-point plan, no controller servers).
- `tools/replay_g8.py` and `tests/test_replay.py` (M6 S13); replay validation
  latency mean 0.0166 ms, p95 ≤0.0352 ms over 21 commands.

### Changed

- `MotionArbiter` now has a single terminal writer and a session lock:
  `_complete_once` is the only place a result and its idempotency entry are
  written; `stop()` completes `ABORTED` but keeps the session active until the
  execute thread exits; `finish()` re-checks the lease before `COMPLETED`;
  `start()` publishes under the lock. C2/C5/C5b now pass on the mock.
- `LeaseManager` is locked and distinguishes `LEASE_REVOKED` from
  `LEASE_EXPIRED`; `SafetySupervisor` transitions are locked.
- The gateway process runs a 20 Hz `TickThread` (`sync_state` + `arbiter.tick`);
  the runtime timer no longer holds the outer lock across `tick`.
- `jcheng_runtime` no longer holds its outer lock across `execute`, `cancel`,
  `/safety_stop` or `tick`; an action cancel can reach the arbiter during a
  motion.
- The roadmap records that the P0 blocker is resolved on the mock and M3 is
  unblocked; the deployed gateway and R5 remain open.
- Gripper completion now follows the baseline semantics: timed completion plus
  bus evidence plus direction/minimum displacement, without an exact pulse
  requirement; the arm keeps the ±8 pulse tolerance.
- Recorded the three pre-fix gripper attempts that latched `UNKNOWN` under the
  exact ±8 pulse criterion (170→183, 194→207, 640→620/619). The timed semantics
  above supersede them; the accepted run is 183→645 close and 647→183 open.
- The bus default rate is 4 Hz; the gateway token is read from the environment;
  shutdown no longer calls `rclpy.shutdown` twice.

### Fixed

- The previous gateway's `measured` field was a command cache
  (`/controller_manager/servo_states`) and differed from the real bus by up to
  387 pulses; the read-only path now uses an independent `GetBusServoState`
  read.

## [0.1.0] — 2026-11-23

M3 mock stack.

### Added

- Pure-Python `jcheng_system_core`: safety state machine, leases, arbitration,
  mock hardware, guard, HTTP gateway, evidence and S1–S17 scenarios.
- Nine ROS packages: core, safety, authority, motion, hardware, gateway, guard,
  testkit and bringup.
- `locks/ecosystem.lock.yaml` and `verify_lock.py`, pinning interfaces v0.1.0.
- Humble `m3-mock` CI and a mock-only repository gate.

## Version policy

The system version is independent of the contract version. `major` signals an
incompatible API/lease/safety change, `minor` a new capability or profile, and
`patch` a defect or documentation fix.
