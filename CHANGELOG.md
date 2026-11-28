# Changelog

All notable changes to this project are documented here. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project follows
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- R11 authority hardening (offline): `jcheng_system_core/hardware_authority.py`
  is the single logical hardware exit. It owns the bridge and checks
  contract, lease/holder/scope, deadman, stop latch, guard and limits before
  `execute_arm`, `submit_base`, `stop` or `status`; unknown state fails closed.
  `jcheng_vendor.executor.HardwareExecutor` now validates and writes through
  it (HTTP behaviour unchanged). `authority.py` gains holder-validated
  renew/release, `deadman_ms` tracking via `note_heartbeat`/`check`,
  `SAFETY_HEARTBEAT_MISSING` rejection, and a revoke stop hook wired in the
  vendor stack/node. `gateway.py` requires `holder`/`client_id` for lease
  DELETE/renew (403 on missing/mismatch) and exempts `/v1/motion/stop` from the
  motion rate limit so stop stays reachable after rate limiting or lease
  expiry. New ROS-free `base.py` (BaseIntent/BaseCommand state machine,
  DISABLED/IDLE/ARMED/MOVING/STOP_LATCHED/FAULT, default-off, configurable but
  unverified 200 ms intent timeout) and `watchdog.py` (independent heartbeat
  watchdog core with latched trip and stop callback). New pure
  `vendor_gate.py` denies DDS `ExecuteMotion` unless
  `JCHENG_ALLOW_DDS_EXECUTE=1`, domain 41, SROS2 enforced and the expected
  topic; `jcheng_vendor/node.py` evaluates it before creating the retained
  ActionServer. Tests: `test_hardware_authority.py`, `test_lease_holder.py`,
  `test_base_authority.py`, `test_watchdog.py`, `test_dds_gate.py`.
  `docs/AUTHORITY-HARDENING.md`. Offline only: no hardware, ROS, DDS or
  `deploy/` change; 200/300 ms values are provisional and unverified.
- R11 base intake (offline): new `jcheng_base` package with a thin,
  `enabled=false`-by-default node on `/jcheng/intent/base` (domain 42). It
  accepts exactly the mobile provisional envelope
  `jcheng.mobile.base_intent.v1` (`schema`, `lease_id`, `holder`,
  `command_id`, `seq`, `stamp_ns`, `frame`, nested `linear`/`angular`,
  `duty_limit`) and rejects unknown schema/frame, flat/legacy shapes, bad
  JSON, stale/future `stamp_ns`, bad lease/holder/scope and latched stops.
  `jcheng_system_core/base_intake.py` holds the ROS-free parser/gate; the
  node's default `UnavailableBaseBridge` refuses every motor-speed write
  instead of fabricating one. No launch file enables it and `deploy/` is
  untouched. Tests: `tests/test_base_intake.py` (21). The envelope is
  repo-local and test-only, not a `jcheng-interfaces` contract.
- R10 hardware gateway deployment: `deploy/armpi-ultra` gains a
  `jcheng-gateway-vendor.service` unit, run script and installer for the
  hardware-backed `/v1` gateway on loopback 8766. The vendor gateway startup
  now defers the first bus read until the executor spins, so it no longer
  deadlocks in `__init__`. The unit is manual-start only, starts `DISARMED`
  and needs a lease; it is never enabled at boot.
  `docs/R10-GATEWAY-20261127.md`; live `/v1` evidence in
  `deploy/armpi-ultra/evidence/20261127-r10/`. No motion command was sent.
- M6 streaming runtime: `jcheng_runtime` accepts `streaming:=true` and builds
  `TeleopIntake(streaming=True, step_period_s=0.05)`. The 20 Hz timer opens one
  arbiter session, publishes frames with `stream` and closes on the neutral
  frame. `tools/teleop_stream_probe.py` drives a 5-joint script; on the mock
  stack all five joints moved ±0.33 rad, and through the domain-43 simulation
  bridge they moved ±0.179 rad (plant lag), with no step warning or latch.
  Evidence: `e2e/reports/20261127_streaming_runtime/`. Mock/simulation only; no
  vendor write.
- M6 streaming intake: `TeleopIntake(streaming=True)` opens one arbiter
  session (`start`), publishes frames with `stream`, and closes on
  deadman/timeout (`finish`) or explicit stop (latched). 20 Hz test: 10 atomic
  frames, release clears without latching, lease revoke aborts/latches.
  `tests/test_streaming_mock.py` (8 tests). Not wired into the ROS runtime.
- M6 arbiter streaming session: `MotionArbiter.stream(command)` publishes
  further frames into a session opened by `start` and closed by
  `finish`/`stop`; every frame re-validates lease/stop/limits/rate, a rejected
  frame aborts terminally, lease loss latches. `tests/test_streaming_arbiter.py`
  (5 tests: frames→COMPLETED, lease revoke, stop, JOINT_LIMIT, no session).
  Mock only; runtime/vendor supervisor not wired.
- M6 atomic stream packing: `jcheng_hardware/streaming.py`
  (`pack_arm_stream`) converts every arm joint to a pulse in mapping order and
  rejects partial/extra/non-finite/out-of-range frames and durations outside
  0.02–0.5 s; `VendorBridge.send_stream` publishes that frame in one
  `ServosPosition` message. `tests/test_stream_pack.py` (10 tests). The
  supervisor loop and vendor-stack exercise remain.
- M6 mock streaming bridge: `jcheng_system_core/streaming.py`
  (`MockStreamingBridge`) records one atomic multi-joint `StreamingFrame` per
  `JOINT_STEP`/`HOME`, with tests for the full-joint frame, deadman release,
  lease revoke, stop latch and a single-joint step that is not a stream.
  `TeleopIntake.last_tick_s` now anchors on the actual tick time instead of
  adding the period (drift/skip found by the cadence test). Evidence:
  `e2e/reports/20261127_streaming_mock/`. Mock only; a runtime supervisor is
  not wired and no vendor write has been made.
- R5: `StopResult` now emits `confirmation` and `physical_stop`; the HTTP
  `StopResponse` carries them. A mock stop that completes an active session as
  `ABORTED` is `backend_confirmed`; an idle stop, or the vendor executor's
  stop-before-terminal path, is `request_accepted`. `physical_stop` is always
  `unverified`. The system contract pin and lock now pin the signed interfaces
  `v0.2.0` tag and hash. Tests: `tests/test_stop_confirmation.py`,
  `tests/test_vendor_executor_c2p.py`.
- R10: `jcheng_vendor/gateway_node.py` and
  `jcheng_bringup/launch/gateway_vendor.launch.py`: a ROS process that owns the
  vendor bridge, requires `enable_motion:=true` and a token, performs an
  initial bus read before serving, polls the bus at about 0.25 s, serves
  `GatewayApp` over HTTP, runs `arbiter.tick`, and routes `/safety_stop` to the
  executor stop. Humble build with the real vendor message definitions passed
  (16 packages, `pytest`, `tests_ros`, M5.2 gate); launch against the live
  vendor stack and hardware C1–C5 remain. A 2026-11-27 read-only precheck found
  8765 reachable but 8766 closed, motion telemetry missing, and the `pi` SSH
  restricted to time sync, so R10 was not deployed; no motion was sent and the
  vendor container was not restarted. Evidence:
  `e2e/reports/20261127_robot_precheck/`.
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

- R11 breaking client changes: `POST /v1/leases` defaults to
  `deadman_required=true` and rejects an explicit false unless the server
  opted out; `POST /v1/motion/execute` requires a `holder` matching the lease;
  `DELETE /v1/leases/{id}` and `POST /v1/leases/{id}/renew` require `holder` or
  `client_id` (body or query). Missing/mismatched holders return 403/422 as
  applicable. Stop responses and motion success payloads are unchanged.
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

- R11 QA authority gaps: lease issuing now requires deadman by default
  (`deadman_required=true`, 300 ms provisional; `deadman_ms=0` rejected), with
  opt-out only through `allow_deadman_opt_out=True`; motion checks reject a
  missing holder with `NOT_LEASE_HOLDER` (legacy only through
  `require_holder=False`); the vendor node gates all `/jcheng/authority/*`
  services and `/jcheng/safety/arm|disarm|reset` behind the same
  `vendor_gate` as `ExecuteMotion` (read-only validate/get_state/capabilities
  remain); `BaseAuthority.tick` now zeroes and latches when the shared safety
  stop or an unknown safety state is seen, even without a base intent.
- R11 base intake unified lease: `BaseIntake` no longer accepts a second
  process-local lease authority. It requires an injected `LeaseView` (or an
  existing `BaseAuthority` whose view it reuses); `GatewayLeaseView` delegates
  live checks to the same `LeaseManager` the gateway uses, so a gateway-issued
  lease is accepted and a lease from a separate manager is rejected. The ROS
  node adds `lease_source=gateway|none` (default `none`), never constructs a
  `LeaseManager`, and fails closed with `RejectAllLeaseView` when no in-process
  shared view is configured. Cross-process lease sharing remains a deployment
  decision and is not implemented.
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
