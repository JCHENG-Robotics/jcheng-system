# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `scripts/humble_ci.sh`: local Humble build/test wrapper (m3, m4, m5-mock,
  m5-vendor, m6) in the `armpi-humble-moveit` image, with a disposable
  workspace copy. CI remains the artifact authority.
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
