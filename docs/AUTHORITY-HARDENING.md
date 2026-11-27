# Authority Hardening — System Implementation

> Status: **offline implementation (2026-11-27)**. This change hardens the
> `jcheng-system` execution authority. It does **not** run on hardware, does
> not start ROS/DDS, does not change `deploy/`, and does not modify the
> `jcheng-interfaces` contract. Software stop is **not** an emergency stop and
> this document does not claim functional-safety compliance.

Related design and audit:
`.github/docs/boundary-consolidation/10-authority-hardening-design.md` and
`02-runtime-authority-audit.md` in the workspace `.github` repository.

## 1. Single logical hardware exit

```text
HTTP /v1/motion/* ─┐
DDS ExecuteMotion ─┤ (disabled by default)
base intent ───────┤
stop / revoke ─────┘
        ▼
HardwareAuthority (jcheng_system_core/hardware_authority.py)
  contract → lease/holder/scope → deadman → stop-latch → guard/limits
        ▼
bridge (VendorBridge or MockHardwareBridge)
        ▼
/servo_controller | set_motor_speeds
```

`HardwareAuthority` owns the bridge and is the only production write path:

| Method | Purpose |
|---|---|
| `execute_arm(command)` | One `JOINT_STEP`/`GRIPPER` write after all checks |
| `submit_base(intent, command)` | One four-channel base duty frame |
| `stop(operator_id, reason)` | Latched software stop: arm hold + base zero |
| `hold_arm()` / `zero_base()` | Stop outputs used by stop/error paths |
| `validate_arm(command)` | Guard + lease + safety/limit report, no write |
| `status()` | Bridge, latch, last duty, stop output status |

Check order before any write (any unknown → fail closed):

1. contract major/hash matches the deployed contract;
2. lease exists and is active;
3. asserted holder matches the lease holder (when a holder is supplied);
4. the lease scope covers the command kind (`arm`, `gripper`, `base`);
5. the deadman window is satisfied for `deadman_ms > 0` leases;
6. the safety state is known and the stop latch is clear;
7. the publisher guard passes;
8. the existing safety limits/kinds/profile checks pass.

`jcheng_vendor.executor.HardwareExecutor` now validates and writes through
`self.hardware_authority`; it no longer calls `send_step`/`send_gripper`/`hold`
directly. HTTP status codes and success payloads are unchanged.

## 2. Lease holder, deadman and revoke-stop

- `LeaseManager.renew(..., holder=...)` and `release(..., holder=...)` reject a
  non-matching holder with `NOT_LEASE_HOLDER` (HTTP 403).
- `note_heartbeat(lease_id, holder=...)` rejects unknown, expired, revoked and
  wrong-holder leases. A lease with `deadman_required=True` gets a `deadman_ms`
  window (default **300 ms, provisional**); issuance seeds the first
  heartbeat. `check()` rejects a missing/stale heartbeat with
  `SAFETY_HEARTBEAT_MISSING` (HTTP 409).
- Successful motion checks use `touch=True`: an accepted command from the
  holder counts as liveness. `tick`/status checks use `touch=False` so a silent
  stream still expires.
- `revoke()` runs an optional stop hook after recording the revocation. The
  vendor stack/node wire it to the executor stop (latch + hold + zero). A
  failing stop hook does not undo the revocation; it records
  `lease_revoke_stop_failed`.

HTTP changes (intentional hardening):

- `DELETE /v1/leases/{id}` and `POST /v1/leases/{id}/renew` now require
  `holder` or `client_id` in the JSON body or query. Missing → 403, mismatch →
  403. `DELETE` with an empty body is no longer accepted.
- `POST /v1/motion/stop` is exempt from the motion request rate limit and does
  not check leases, so a stop remains reachable after rate limiting or lease
  expiry. It still requires the gateway token.

## 3. Base intent state machine (`base.py`, ROS-free)

`BaseIntent` → `BaseAuthority` → `BaseCommand` (four normalized duties).
All-zero duty means stop. The module is independent of ROS and of the arm
executor; the only output is the injected sink.

| State | Meaning | Output | Exits |
|---|---|---|---|
| `DISABLED` | intake off (default) | zero | config enable/restart |
| `IDLE` | enabled, no accepted intent | zero | valid intent → `ARMED`/`MOVING`; stop → `STOP_LATCHED` |
| `ARMED` | valid intent/lease seen, not driving | zero | fresh non-zero intent → `MOVING`; timeout stays; lease/deadman loss → `STOP_LATCHED` |
| `MOVING` | fresh intent driving | duty frame | stream timeout → `ARMED` (+zero); lease/deadman loss → `STOP_LATCHED` |
| `STOP_LATCHED` | stop/lease/deadman/policy latch | zero | operator `reset()` → `IDLE`; still blocked by the shared safety latch |
| `FAULT` | unknown state/contract/output failure | zero | operator `reset()` → `IDLE` |

Event behaviour:

| Event | Behaviour |
|---|---|
| no lease | `STOP_LATCHED`, zero, reject |
| wrong holder or lease scope | `STOP_LATCHED`, zero, reject |
| lease expired/revoked | `STOP_LATCHED`, zero, reject |
| deadman missing/stale | `STOP_LATCHED`, zero, reject |
| intent timeout (`intent_timeout_ms`) | zero, `ARMED`; next valid intent may resume |
| network/link loss | zero, `IDLE`; a higher fresh `seq` is required to resume |
| duplicate/stale `seq` or command id | rejected, no new output |
| concurrent submits | serialized by a reentrant lock; highest accepted `seq` wins; no torn duty frame |
| explicit stop | `STOP_LATCHED`, zero, shared safety latch |
| arm stop (shared safety latch) | base refuses intent and latches/zeroes |
| restart | zero, `IDLE`, `seq` reset, no replay until a new intent |
| unknown restored state | `FAULT`, zero |
| incompatible contract | `FAULT`, zero |
| malformed/legacy intent (no holder/contract) | rejected without a write |

`intent_timeout_ms` defaults to **200 ms**. This value is configurable but is
**not validated** as a safe stop latency on hardware. The mecanum mixer
(`speed_scale_mps`, `track_half_width_m`) is also provisional.

The base intake is **not wired** to a ROS node or HTTP route in this change;
`BaseConfig.enabled` defaults to `False`.

## 4. Watchdog (`watchdog.py`)

`HeartbeatWatchdog` is a pure core with an injected clock and an optional
timeout callback. It does not depend on the upper execution loop: a separate
process/thread calls `poll()` and the callback must publish zero/stop (never a
motion). A trip is latched; only an explicit `arm()` clears it, and a
heartbeat after a trip does not. Callback errors are recorded but do not clear
the trip.

The watchdog is not yet installed as a systemd unit; `deploy/` is intentionally
untouched.

## 5. DDS ExecuteMotion gate (`vendor_gate.py`)

The DDS action is test-only and denied by default. The pure gate allows it
only when all hold:

- `JCHENG_ALLOW_DDS_EXECUTE` is exactly `"1"`;
- the ROS domain is the privileged domain **41**;
- SROS2 is enabled (`ROS_SECURITY_ENABLE`), a keystore is set
  (`ROS_SECURITY_KEYSTORE`) and `ROS_SECURITY_STRATEGY=Enforce`;
- the ActionServer topic is `/jcheng/motion/execute`.

`jcheng_vendor/node.py` evaluates the gate before constructing `ActionServer`.
On denial the node logs the reason and leaves `_action_server = None`; the
ActionServer code is retained for an explicitly authorized deployment.

## 6. Stop responsibilities

| Mechanism | Scope | Claim |
|---|---|---|
| Base all-zero frame / arm hold | software output | requested; physical effect unverified |
| `HardwareAuthority.stop` | arm hold + base zero + safety latch | software stop, not an emergency stop |
| Safety stop latch | rejects new motion until operator reset | software interlock |
| Watchdog timeout callback | zero/stop output | software supervision; not independent safety hardware |
| Physical E-stop / power isolation | hardware | outside this repository; not implemented or claimed here |
| Bus verification | measured pulse read-back | evidence only; not a safety rating |

"Stop accepted" means the software path latched and issued the stop outputs.
It does **not** mean the chassis or arm physically stopped; `physical_stop`
remains `unverified`.

## 7. Tests (all offline)

| File | Focus |
|---|---|
| `tests/test_hardware_authority.py` | no lease, holder/scope, expiry/revoke, deadman, contract, unknown state/kind, base limits, stop coupling |
| `tests/test_lease_holder.py` | renew/release holder, unknown/expired/revoked, deadman heartbeat, revoke hook, HTTP holder, stop reachability |
| `tests/test_base_authority.py` | full base state machine and fault matrix, concurrency, restart, link loss, legacy intent |
| `tests/test_watchdog.py` | trip latch, blocked upper loop, gateway-exit heartbeat loss, callback failure, base zero output |
| `tests/test_dds_gate.py` | default deny, opt-in/domain/SROS2/topic, node wiring order |

No test starts ROS, DDS, a device, or a robot.

## 8. Unverified / not covered

- The 200 ms base intent timeout, 300 ms lease deadman default and the
  mecanum mixer are design values, not measured safety limits.
- Arm hold at the last measured pulse and base zero-duty semantics are not
  confirmed on the vendor/motor board.
- No hardware or bench validation, no stop-latency measurement, no physical
  E-stop integration.
- `VendorBridge` has no base motor-speed output yet; `submit_base` fails closed
  (`HARDWARE_UNAVAILABLE`) if the bridge lacks `publish_motor_speeds`.
- Base intake is not wired to ROS/HTTP and is disabled by default.
- The watchdog core is not deployed as a unit; the Pi host/container placement
  decision is still open.
- The DDS gate depends on the process environment; it is not an SROS2
  certificate verification implementation.
- `MotionArbiter`/`MockHardwareBridge` remain the simulation path and still
  write the mock bridge directly; they are not a production hardware exit.
  `MockStack` does not install a revoke stop hook, so its arbiter detects the
  revoked lease on the next tick/frame and keeps the existing `LEASE_REVOKED`
  terminal result; the vendor stack/node do install the stop hook.
- The mock `jcheng_runtime` ActionServer is not gated in this change (it is a
  MockStack test transport with no hardware write path); only the vendor
  hardware runtime is gated.
- The Studio/mobile repos and `deploy/` were not changed.

## 9. Rollout and rollback

- Base intake: default `enabled=False`; enabling it is an explicit config
  change and requires a base-capable bridge.
- DDS action: default denied; enabling requires the full env/SROS2/domain
  combination above.
- Lease holder on HTTP renew/DELETE is a breaking client change; clients must
  send `holder`/`client_id`.
- Rollback is a code revert; no data or contract migration is involved.
