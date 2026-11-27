# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- R10: `jcheng_vendor/gateway_node.py` and
  `jcheng_bringup/launch/gateway_vendor.launch.py`: a ROS process that owns the
  vendor bridge, requires `enable_motion:=true` and a token, performs an
  initial bus read before serving, polls the bus at about 0.25 s, serves
  `GatewayApp` over HTTP, runs `arbiter.tick`, and routes `/safety_stop` to the
  executor stop. Compile-checked; Humble build and hardware C1–C5 pending.
- R9: `jcheng_vendor.gateway_stack.VendorStack` composes the M5.2
  `HardwareExecutor` with the HTTP `GatewayApp`; `VendorGatewayHardware`
  adapts the read surface, and `mode="hardware"` / `capabilities=("hardware",)`
  replace the hard-coded mock advertisement. `MockStack` keeps the mock mode.
  Offline `tests/test_vendor_gateway_stack.py` drives `/v1/motion/execute` to a
  bus-verified `COMPLETED`. The ROS process that owns `VendorBridge` and polls
  the bus is still required.
- R8: `jcheng_vendor.executor.HardwareExecutor` now orders the bridge send and
  the stop under the same `_active_lock`. A stop accepted before the send
  publishes no command; a stop after the send orders its hold after the write.
  `tests/test_vendor_executor_c2p.py` covers both interleavings and a normal
  bus-verified completion. Evidence:
  `e2e/reports/20261126_vendor_executor_c2p/`.
- R7 offline finding against the deployed `armpi_debug_gateway`:
  `tools/deployed_guard_race_probe.py` reproduces a lost stop between
  `MotionGuard.validate()` and `start()`; the command can then complete. No
  ROS or hardware is used. Evidence:
  `e2e/reports/20261126_deployed_guard_race/`. The frozen baseline is not
  edited; the chosen target is the M5.2 executor (R8/R9), so this remains a
  legacy-gateway risk.
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
- `jcheng_runtime/sim_bridge.py`: test-only `RosSimHardwareBridge` from the
  system mock hardware port to `/sim/joint_command`/`/sim/joint_states` on
  domain 43. `sim_bridge:=true` enables it; no hardware path.
- `tools/teleop_sim_probe.py` and `e2e/reports/20261126_teleop_sim/`: ArmPi
  L3 probe (teleop -> intake -> simulation bridge), arm joint1 0 -> 0.205 rad;
  domain 43, test-only.
- `docs/TELEOP-INTAKE.md`: M2 scope, limits and open items.
- `e2e/reports/20261126_teleop_humble/`: Humble container build and tests
  (`tests` 45 pass, `tests_ros` 12 pass).
- `e2e/reports/20261126_teleop_sim_gate2/`: Gate-2 L3 probe, both joints
  moved to about ±0.501 rad; domain 43, test-only.
- `e2e/reports/20261126_teleop_domain42/`: canonical-domain L3 for Gate-2.
  Runtime/teleop on domain 42; `RosSimHardwareBridge` in a separate domain-43
  context; both joints moved to about ±0.125 rad.
- Optional `servo_id`/`gripper`/`workspace`/`table_plane` profile fields and
  simulation-only Gate-2 and PiPER X profiles; `MockStack.build(robot=...)` and
  the runtime `robot` parameter select them. `tests/test_teleop.py` covers
  two-joint and six-joint steps. Gate-2 L3 moves both joints to about ±0.501 rad;
  PiPER X L3 is still not proven and v1 is not canonical for a CAN arm.
- Read-only `/jcheng/telemetry/state` tap in `jcheng_runtime`: a `RobotState`
  at the 20 Hz tick, published next to the safety topic for capability
  consumers. The mock backend is relabelled `commanded`/`timed` because its
  fixture claims `measured`/`bus` to exercise the safety path; the simulation
  bridge keeps `measured`/`simulated`. `tests_ros/test_teleop_runtime.py` adds
  label and field tests. Test-only; not the production telemetry node.
- `e2e/reports/20261126_teleop_state_telemetry/`: label matrix, tests and the
  teleop capture consumer evidence.

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

- M4/M5 motion target (2026-11-26): the `jcheng_system_core` gateway with the
  M5.2 `jcheng_vendor` hardware executor. The frozen `armpi_debug_gateway` is
  legacy and not the motion authority; its R7 race is recorded but not fixed.
- R5 is merged as a contract proposal in `jcheng-interfaces` (#8):
  `StopResponse` would add `confirmation` (request accepted / backend
  confirmed) and `physical_stop: unverified`. No schema, tag or contract hash
  change yet.
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
