# jcheng-system

The privileged runtime and system integration point for JCHENG Robotics.

> Status: **Experimental** — v0.3.0-dev as of 2026-11-25. M4 read-only hardware
> and M5.2 core motion are verified; M6 offline replay and real MoveIt plan-only
> are verified. S7 (stale-state injection) and S9 (runtime publisher detection)
> are not verified on hardware. The runtime is not safety-certified.
> English canonical; see [README.zh-CN.md](README.zh-CN.md) for the Chinese translation.

## 1. Deliverables

| Package | Role |
|---|---|
| `jcheng_system_core` | Pure-Python mock core: config, safety state machine, leases, arbitration, mock hardware, guard, gateway, evidence, scenarios |
| `jcheng_safety` | Safety diagnostic CLI |
| `jcheng_authority` | Lease diagnostic CLI |
| `jcheng_motion` | Arbiter diagnostic CLI |
| `jcheng_hardware` | Mock hardware CLI + read-only bus bridge (`readonly.py`) |
| `jcheng_gateway` | Mock HTTP gateway entry point |
| `jcheng_guard` | Publisher-allowlist diagnostic |
| `jcheng_testkit` | S1–S17 scenario runner |
| `jcheng_telemetry` | Read-only telemetry cache, read-only gateway, preflight, evidence, short probe |
| `jcheng_runtime` | M5.1 ROS transport: lease/safety/validation services, `ExecuteMotion` action, stop latch (mock) |
| `jcheng_vendor` | M5.2 supervised hardware runtime: single joint/gripper, `/servo_controller`, bus verification |
| `jcheng_moveit` | M6 S17 plan-only goal construction/validation; does not execute MoveIt |
| `jcheng_bringup` | `mock_stack`, `readonly`, `motion_mock` and `motion_vendor` launch files |

## 2. Architecture

```text
HTTP client
  → jcheng_gateway (token / rate limit)
  → jcheng_system_core.MotionArbiter
      → LeaseManager
      → SafetySupervisor
      → PublisherGuard
      → HardwareBridge (mock or vendor)
      → EvidenceStore
```

- Only the hardware bridge publishes commands; the guard allowlist permits
  `jcheng_hardware`.
- Completion comes from a bus read, not from the command cache; a bus failure
  latches `UNKNOWN`.
- The contract hash is pinned to interfaces v0.1.0:
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`.

### 2.1 M4 read-only chain

```text
vendor nodes (domain 0)
  ├─ /controller_manager/joint_states  → truth=commanded
  ├─ /ros_robot_controller/battery
  └─ /ros_robot_controller/bus_servo/get_state (GetBusServoState, read-only position)
        → jcheng_telemetry (domain 0)
            → /jcheng/telemetry/state (RobotState)
            → /jcheng/telemetry/safety (SafetyState, read-only view)
            → 127.0.0.1 read-only HTTP gateway (GET only)
```

- Only `RobotState`/`SafetyState` are published; no command publisher exists.
- Only `GetBusServoState` is called with `get_position=1`; servo IDs 1..6 in
  strict order.
- No serial/device access, no vendor-container restart, no enable, lease or
  motion.
- The M4 telemetry runs in domain 0; domain 41/42 bridging is prepared but not
  implemented.

### 2.2 M5.1 ROS transport (mock)

```text
HTTP/CLI client
  → /jcheng/authority/*_lease        (LeaseManager)
  → /jcheng/safety/validate_motion   (SafetySupervisor, validation only)
  → /jcheng/safety/{arm,disarm,reset,get_state}
  → /jcheng/motion/execute           (ExecuteMotion action)
      → MotionArbiter → MockHardwareBridge
  → /safety_stop (Bool, true)        → STOP_LATCHED
```

- Default state is `DISARMED`; arming needs a checklist and confirmation
  phrase.
- Motion requires a valid lease; missing, expired, preempted or revoked leases
  are rejected.
- `ValidateMotion` validates only; it does not execute or publish.
- `/safety_stop=true` latches immediately and rejects new commands.
- M5.1 uses `MockStack` only: no vendor command publisher, no write service, no
  device access.
- M5.2 adds the `/servo_controller` bridge; the single joint and gripper passed
  core hardware acceptance. The gripper uses timed completion plus bus
  evidence; the arm keeps the ±8 pulse tolerance.

## 3. Requirements

- ROS 2 Humble on the robot; Jazzy is used for local host builds.
- Python ≥ 3.10, `pytest`, `PyYAML`.
- No hardware for the mock and read-only offline paths; the vendor path requires
  the robot and separate authorization.

## 4. Quick start

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash

# 28 contract/scenario/read-only tests (27 pass / 1 conditional skip)
python3 -m pytest -q

# M5.1 mock ROS transport: 5 service/action/stop tests
python3 -m pytest -q tests_ros

# M5.1 mock runtime (lease, safety, validation, action; no hardware)
ros2 launch jcheng_bringup motion_mock.launch.py root:="$PWD"

# S1–S17 (15 pass / 2 skipped)
ros2 run jcheng_testkit jcheng_scenarios --root . \
  --out e2e/reports/m3-mock.json

# Short read-only probe (mock; no robot)
ros2 run jcheng_telemetry jcheng_readonly_probe \
  --root . --duration-s 3 --interval-s 0.2 \
  --out e2e/reports/m4-readonly.json \
  --evidence e2e/reports/m4-readonly.jsonl
```

The mock gateway needs a token:

```bash
ros2 run jcheng_gateway jcheng_gateway --port 8766 --token "<secret>"
# or
ros2 launch jcheng_bringup mock_stack.launch.py token:="<secret>"
```

Mock gateway endpoints: `/v1/health`, `/v1/contract`, `/v1/state`,
`/v1/safety`, `/v1/leases`, `/v1/motion/validate`, `/v1/motion/execute`,
`/v1/motion/stop`, `/v1/capabilities`, `/v1/events`.
Read-only gateway endpoints: `/v1/health`, `/v1/contract`, `/v1/state`,
`/v1/safety`, `/v1/battery`, `/v1/topics`, `/v1/cameras`, `/v1/tf`,
`/v1/events`. Every non-GET method returns 403; there are no motion, lease,
arm or reset routes.

## 5. Safety boundary

- Default `DISARMED`; arming requires an operator, checklist and confirmation
  phrase.
- Stale state, a latched stop, a profile/contract-hash mismatch, a missing
  lease, an unauthorized publisher or a bus failure is rejected or latched.
- Stop is a software stop, not an emergency stop.
- The platform has no e-stop, brake, independent encoder or self-collision
  model.
- The M4 read-only chain has no command publisher, write service or device
  access.
- `/controller_manager/joint_states` is labelled `commanded` and is not a
  completion source.
- Every real-robot motion requires separate, explicit on-site authorization.

## 6. Validation status

| Claim | Status | Evidence |
|---|---|---|
| Mock scenarios S1–S17 (required) | Verified (L2–L3) | 15 pass, 2 skipped; `e2e/reports/m3-mock.json` |
| Read-only integration writes nothing | Verified (L5) | 30 min, 1799/1799 `measured`, `writes=0` |
| Mock ROS lease/safety/action/stop | Verified (L2–L3) | 5 `tests_ros`; CI `m5-mock` |
| Supervised joint/gripper motion | Verified, core (L5) | 505 vs 512 pulses; gripper 183→645 / 647→183 |
| S13 replay decisions | Verified (L4) | 21/21; validate mean 0.0166 ms, p95 ≤0.0352 ms |
| S17 plan-only construction | Verified (L1) | `test_moveit_plan_only.py` |
| Real MoveIt plan-only (S17) | Verified (L5) | 2026-11-25: `SUCCESS`, 8-point plan, 0 controller servers, no command topics |
| S14 base, full G8 | Unverified | Not run |
| S7 stale-state injection, S9 runtime publisher | Unverified | Not run on hardware |

Three earlier gripper attempts, made under the exact ±8 pulse criterion before
the baseline timed-completion semantics were adopted, latched `UNKNOWN`
(170→183, 194→207, 640→620/619 pulses). The accepted result is the final run in
the table; the failed attempts are kept as history in
[M5 scope](docs/M5-SCOPE.md).

Full report: [ACCEPTANCE.md](ACCEPTANCE.md).

## 7. Related repositories

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — contract and hash.
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — frozen `v1.0.0` baseline and design record.
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — health and recovery.
- [`.github`](https://github.com/JCHENG-Robotics/.github) — ecosystem roadmap, changelog and acceptance.

## 8. License

The license review is in progress; this repository has no `LICENSE` and all
rights are reserved. See [LICENSE-REVIEW.md](LICENSE-REVIEW.md).
