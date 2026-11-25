# Acceptance report — jcheng-system

> Status: **inconclusive** as of 2026-11-25 for v0.3.0-dev.
> Operator: JerichoCheng. Reviewer: pending.
> Scope: M3 mock stack, M4 read-only integration, M5.1 mock transport,
> M5.2 supervised hardware motion and M6 offline replay/plan-only, plus the
> real MoveIt plan-only check.
> The completed gates pass; S14/G8 execution, S7/S9 and trajectory execution
> have not run, so the system acceptance is not complete.
> No motion was sent by this documentation change.

## 1. Acceptance layers

| Level | Name | Environment | Hardware |
|---|---|---|---|
| L0 | Contract / static | CI runner | No |
| L1 | Unit | CI runner | No |
| L2 | Mock integration | In-process | No |
| L3 | Mock end-to-end | Mock gateway/topics | No |
| L4 | Replay | Recorded data | No |
| L5 | Hardware | Robot | Yes |

## 2. Gate results

| Gate | Level | Result | Evidence |
|---|---|---|---|
| M3 scenario runner (S1–S17) | L3 | pass — 15 passed, 2 skipped, 0 required failures | `e2e/reports/m3-mock.json` |
| M3 pytest | L1 | pass — 16 tests | CI `m3-mock` |
| M4 offline pytest | L1 | pass — 28 tests (27 pass, 1 conditional skip) | CI `m4-readonly` |
| M4 read-only probe | L2 | pass — `writes=0`, measured samples > 0 | `e2e/reports/m4-readonly.json` |
| M4 hardware read-only | L5 | pass — 30 min, 1799/1799 `measured`, `writes=0` | `jcheng-manipulation/docs/ecosystem/evidence/m4-readonly-20261123/` |
| M5.1 ROS transport | L2–L3 | pass — 5 tests | `tests_ros/`; CI `m5-mock` |
| M5.2 hardware motion | L5 | inconclusive — joint, gripper, stop, lease/limit rejection pass; S7/S9 not run | `jcheng-manipulation/docs/ecosystem/evidence/m52-final-20261124/` |
| M6 S13 replay | L4 | pass — 21/21 decisions; validate mean 0.0166 ms, p95 ≤0.0352 ms | `jcheng-manipulation/docs/ecosystem/evidence/m6-s13-20261124/` |
| M6 S17 plan-only | L1 | pass — construction and rejection tests | `tests/test_moveit_plan_only.py` |
| M6 S17 real MoveIt plan-only | L5 | pass — MoveIt 2.5.10, `SUCCESS`, 8-point plan, 0 controller servers, no command topics | `jcheng-manipulation/docs/ecosystem/evidence/m6-s17-20261125/` |

## 3. Verified claims

### M3 — mock stack (2026-11-23)

- `jcheng_testkit` S1–S17: 15 passed, 2 skipped, 0 required failures; S13 and
  S17 were the planned skips.
- 16 `pytest` tests; Humble `m3-mock` built interfaces + 9 system packages.
- `verify_lock.py`: interfaces v0.1.0, contract hash and profile hash match the
  remote tag.
- No non-`jcheng_hardware` publisher; stop, stale state, limits, workspace,
  table, guard and bus failures reject or latch.

### M4 — read-only, offline (2026-11-23)

- 28 `pytest` tests pass (one conditional skip), including read-only cache,
  bridge, gateway, preflight, evidence and the short probe.
- `jcheng_readonly_probe`: `writes=0`, `measured_samples>0`, preflight
  `ok=true`.
- `scripts/check_m4_readonly.sh`: no command publisher, no write service, no
  device path.

### M4 — read-only, hardware (2026-11-23)

- 30 minutes (1798 s): **1799/1799 samples `truth=measured`,
  `verification=bus`, `writes=0`, `ok=true`**.
- Independent `GetBusServoState` readings match the new node item by item. The
  old gateway's `measured` was `/controller_manager/servo_states` (a command
  cache) and differed from the real bus by up to 387 pulses; M4 uses the
  independent bus read as the reference.
- Domain 42 sees only `/parameter_events` and `/rosout`; no vendor command
  topic.
- `/jcheng/telemetry/state` has one publisher, `jcheng_telemetry`; no command
  publisher exists.
- The camera `/depth_cam/rgb/image_raw` had no publisher at the time; `/tf` was
  healthy; `/tf_static` TRANSIENT_LOCAL QoS was not yet adapted.
- No motion command was sent; the vendor container was not restarted.

### M5.1 — mock ROS transport (2026-11-24)

- 5 `tests_ros` pass: service round-trip, lease, safety arm, stop latch, action
  completion with bus confirmation.
- `scripts/check_m5_mock.sh` passes: runtime uses `MockStack` only; no vendor
  command publisher, no write service, no device path.
- `m5-mock` builds interfaces + 11 system packages and runs `pytest` and
  `tests_ros`.
- No robot connected, no motion command.

### M5.2 — supervised hardware (2026-11-24, core)

- Preconditions: `DISARMED` → `ARMED_IDLE`, lease granted,
  `ValidateMotion=approved`.
- joint1 +0.05 rad: `COMPLETED`, `bus_verified=true`; bus 505 vs target 512
  (7 pulses, tolerance 8).
- Gripper close 183→645 (target 700) and open 647→183 (target 170): both
  `COMPLETED`, `bus_verified=true`. The gripper uses the baseline semantics:
  timed completion + bus evidence + direction/minimum displacement, without an
  exact pulse requirement.
- History: three earlier attempts under the exact ±8 pulse criterion latched
  `UNKNOWN` (170→183, 194→207, 640→620/619); see
  [M5 scope](docs/M5-SCOPE.md) and `docs/ACCEPTANCE.zh-CN.md`.
- Stop: `ABORTED`/`STOP_LATCHED`, hold 0.266 ms, client 48.4 ms. After the stop
  the action server no longer exists and new commands are rejected.
- No lease → `NO_LEASE`; out of limit → `JOINT_LIMIT`; both rejected.

### M6 — offline (2026-11-24)

- `g8_acceptance_20261123/g8_run.json`: 11/11 commands have the same decision.
- `studio_g8_fast/20261121/run2.json`: 10/10 commands agree.
- The only limit difference is the profile's four-decimal rounding versus the
  baseline value (max 5.9e-5 rad); replay boundary normalization is ≤1e-3 rad
  and recorded. Runtime safety checks were not modified.
- `tests/test_replay.py` passes synthetic valid/invalid cases.
- `jcheng_moveit/plan_only.py` constructs and validates `plan_only=true` and
  rejects execute/trajectory; `tests/test_moveit_plan_only.py` passes.
- Validation latency over the 21 replayed commands: mean 0.0166 ms,
  p95 ≤0.0352 ms, max 0.0352 ms.
- No motion was executed.

### M6 — real MoveIt plan-only (2026-11-25)

- MoveIt 2.5.10 arm64 (`ros-humble-moveit-ros-move-group`) with the vendor
  `armpi_ultra_moveit_config` 0.3.0 on the ArmPi container; domain 42,
  CycloneDDS, OMPL RRTConnect.
- `move_group.launch.py allow_trajectory_execution:=false`; no controller
  manager was started.
- One `MoveGroup` goal (`group=arm`, start supplied, target `joint1=0.10`,
  `plan_only=true`) returned `SUCCESS`, 8 trajectory points, planning time
  0.050016 s.
- No execution path: `/arm_controller` and `/gripper_controller`
  FollowJointTrajectory actions had 0 servers, no command topics existed, and
  no `/execute_trajectory` goal was sent.
- The vendor config requests TRAC-IK; no arm64 package exists, so the local
  copy uses KDL with `position_only_ik=true` (original preserved as
  `kinematics.yaml.trac-ik`). This is a local config adaptation, not a
  repository change.
- Evidence: `jcheng-manipulation/docs/ecosystem/evidence/m6-s17-20261125/`.
- No robot motion command was sent.

## 4. Not verified

| Item | Reason | Risk |
|---|---|---|
| S7 stale-state injection | Not run on hardware | Stale state may not be rejected as designed |
| S9 runtime unauthorized publisher | Not run on hardware | Publisher guard is not proven at runtime |
| Trajectory execution via MoveIt | Plan-only was requested; no controller server exists | Execution integration is unproven |
| Pose/Cartesian goals and obstacles | Only one joint-space goal was planned | Planner behavior may differ for other goals |
| S14 hardware base | Not run | Base intent path unproven |
| Full G8 re-run | Not run | End-to-end system path unproven |
| Domain 0 → 41/42 bridge | Not implemented | Capabilities cannot consume telemetry without joining domain 0 |
| `/tf_static` TRANSIENT_LOCAL QoS | Not adapted | Static transforms may be missed |
| Unattended operation | Requires an operator; vendor restart moves the arm | Not suitable for unattended motion |

## 5. Limitations

- M3 evidence is mock; M4 is read-only; M5.2 core motions and the S17 real
  plan-only check are the hardware results.
- S17 used KDL rather than the vendor's TRAC-IK because no arm64 TRAC-IK package
  exists; the planning result is real, but the IK plugin differs from the vendor
  configuration.
- S7/S9 residuals remain open.
- The telemetry runs in vendor domain 0; domain isolation is not yet complete.
- No e-stop, brake, independent encoder or self-collision model.
- The base is open-loop with no odometry.
- The license review is pending; no `LICENSE` exists.

## 6. Sign-off

| Role | Name | Date |
|---|---|---|
| Operator | JerichoCheng | 2026-11-25 |
| Reviewer | pending | — |

## 7. Machine-readable report

None. Per-run reports, when produced, must validate against
`jcheng-interfaces/schemas/acceptance-report.v1.schema.json`.
