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
3. the asserted holder is present and matches the lease holder (missing or
   wrong → `NOT_LEASE_HOLDER`);
4. the lease scope covers the command kind (`arm`, `gripper`, `base`);
5. the deadman window is satisfied for every default-issued lease;
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
  wrong-holder leases. **Deadman is required by default**: every issued lease
  gets a `deadman_ms` window (default **300 ms, provisional**) unless the
  manager was deliberately configured for opt-out; issuance seeds the first
  heartbeat. `deadman_ms=0` is rejected when deadman is required.
  `check()` rejects a missing/stale heartbeat with
  `SAFETY_HEARTBEAT_MISSING` (HTTP 409).
- Deadman opt-out is a deliberate config/test flag only:
  `LeaseManager(allow_deadman_opt_out=True)` (plus an explicit
  `deadman_required=False`) and records `lease_deadman_opt_out`. The legacy
  mock runtime sets this flag explicitly; normal issuers do not.
- Successful motion checks use `touch=True`: an accepted command from the
  holder counts as liveness. `tick`/status checks use `touch=False` so a silent
  stream still expires.
- **Motion checks must assert the holder.** A lease always has a holder, so a
  missing command holder is rejected with `NOT_LEASE_HOLDER` (`holder is
  required`) rather than skipping the check; a wrong holder is a distinct
  rejection. Status/observability callers may pass `require_holder=False`.
  Legacy compatibility requires an explicit `LeaseManager(require_holder=False)`
  (the mock runtime uses it) or a per-call status opt-out.
- `revoke()` runs an optional stop hook after recording the revocation. The
  vendor stack/node wire it to the executor stop (latch + hold + zero). A
  failing stop hook does not undo the revocation; it records
  `lease_revoke_stop_failed`.

HTTP changes (intentional hardening):

- `POST /v1/leases` defaults `deadman_required=true`. An explicit
  `deadman_required=false` is rejected with `INVALID_COMMAND` unless the
  server's manager has `allow_deadman_opt_out=True`.
- `POST /v1/motion/execute` commands must include `holder` matching the lease
  holder; a missing or wrong holder is rejected with `NOT_LEASE_HOLDER`.
- `DELETE /v1/leases/{id}` and `POST /v1/leases/{id}/renew` require
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
| arm stop (shared safety latch) | base refuses intent; `tick` also zeroes and latches without a base intent |
| unknown shared safety state | `tick` zeroes and latches `STOP_LATCHED` |
| restart | zero, `IDLE`, `seq` reset, no replay until a new intent |
| unknown restored state | `FAULT`, zero |
| incompatible contract | `FAULT`, zero |
| malformed/legacy intent (no holder/contract) | rejected without a write |

`intent_timeout_ms` defaults to **200 ms**. This value is configurable but is
**not validated** as a safe stop latency on hardware. The mecanum mixer
(`speed_scale_mps`, `track_half_width_m`) is also provisional.

The base intake node is gated and disabled by default (section 4);
`BaseConfig.enabled` defaults to `False`.

## 4. Provisional mobile base intake (`jcheng_base`, test-only)

The thin node `ros2_ws/src/jcheng_base/jcheng_base/node.py` subscribes to
`/jcheng/intent/base` and accepts the exact mobile provisional envelope from
`mobile intent.py::to_dict`:

```json
{
  "schema": "jcheng.mobile.base_intent.v1",
  "lease_id": "lease-...",
  "holder": "nav2",
  "command_id": "cmd-...",
  "seq": 1,
  "stamp_ns": 1000000000,
  "frame": "base_link",
  "linear": {"x": 0.05, "y": 0.0},
  "angular": {"z": 0.0},
  "duty_limit": 1.0
}
```

Rules:

- `schema` must equal `jcheng.mobile.base_intent.v1`; any other schema is
  rejected. This schema is the envelope version gate.
- `frame` must be `base_link`.
- `stamp_ns` is the mobile **monotonic** timestamp (not a header stamp),
  compared with `time.monotonic_ns()` at receipt. Mobile and system receive
  time must therefore be on the **same host** and the same monotonic clock
  domain; a cross-host envelope has no valid shared monotonic reference. The
  default freshness window is 200 ms old / 50 ms in the future, configurable
  and **provisional**.
- `linear`/`angular` must be objects with finite numeric `x`/`y` and `z`.
- `duty_limit` is normalized to 0..1 and **clamped** (a negative value becomes
  a stop/zero command). It scales the profile base duty cap.
- `seq` must be a non-negative integer and strictly increase; duplicate/old
  seq and duplicate command ids are rejected without a new output.
- The envelope intentionally carries **no** `contract`, `deadman_ms`,
  `source` or `max_duty_normalized`. The intake uses the locally deployed
  contract; envelope-version incompatibility is expressed by `schema`.
- Lease, holder, scope, deadman and stop-latch checks are still performed by
  `BaseAuthority`; a stop latches and later envelopes are rejected.

The node declares `enabled=false` by default, creates no launch file that
enables it, and is not part of `deploy/`. When disabled or on a non-42 ROS
domain it rejects messages with `DISABLED`/`WRONG_DOMAIN` and submits nothing.
The node's default bridge is `UnavailableBaseBridge`, so it never fabricates a
motor-speed frame: every desired write refuses with `HARDWARE_UNAVAILABLE`
until a reviewed base bridge exists.

### Unified lease requirement

There is exactly **one** lease authority per system: the `LeaseManager` that
the gateway owns. `BaseIntake` never constructs a manager; it requires an
injected `LeaseView` and builds `BaseAuthority`/`HardwareAuthority` against
that same view. `GatewayLeaseView` delegates every check live to the gateway's
own manager instance, so a lease acquired through the gateway is visible to
the base intake and a lease from a separate manager is rejected as
`NO_LEASE`. A process-local `LeaseManager` is a test double only, never an
accepted base-intake design.

The ROS node exposes `lease_source=gateway|none`, default **`none`**:

- `none` uses `RejectAllLeaseView` and rejects every envelope; it never falls
  back to a local lease.
- `gateway` requires an in-process `GatewayLeaseView` injected into the node.
  A standalone node cannot obtain the gateway's memory, so without that
  shared view it fails closed (`gateway-unavailable`) and rejects everything.
- Any other value is treated as `invalid` and fails closed.

Cross-process lease sharing (for example an IPC lease service or a shared
daemon) is a **deployment decision** and is not implemented here.

This envelope is a **repo-local, test-only** format. It is **not** a
`jcheng-interfaces` contract and must not be treated as one; adopting it
requires an accepted interfaces proposal.

## 5. Watchdog (`watchdog.py`)

`HeartbeatWatchdog` is a pure core with an injected clock and an optional
timeout callback. It does not depend on the upper execution loop: a separate
process/thread calls `poll()` and the callback must publish zero/stop (never a
motion). A trip is latched; only an explicit `arm()` clears it, and a
heartbeat after a trip does not. Callback errors are recorded but do not clear
the trip.

The watchdog is not yet installed as a systemd unit; `deploy/` is intentionally
untouched.

## 6. DDS privileged surface gate (`vendor_gate.py`)

The privileged DDS surface is test-only and denied by default. It covers the
`ExecuteMotion` ActionServer **and** these services:

- `/jcheng/authority/acquire_lease`, `renew_lease`, `release_lease`,
  `revoke_lease`;
- `/jcheng/safety/arm`, `/jcheng/safety/disarm`, `/jcheng/safety/reset`.

The pure gate allows the surface only when all hold:

- `JCHENG_ALLOW_DDS_EXECUTE` is exactly `"1"`;
- the ROS domain is the privileged domain **41**;
- SROS2 is enabled (`ROS_SECURITY_ENABLE`), a keystore is set
  (`ROS_SECURITY_KEYSTORE`) and `ROS_SECURITY_STRATEGY=Enforce`;
- the ActionServer topic is `/jcheng/motion/execute`.

`jcheng_vendor/node.py` evaluates the gate once before creating any privileged
service or the ActionServer. Denied services are not advertised; the code is
retained for an explicitly authorized deployment. The read-only
`validate_motion`, `get_state` and `capabilities` services remain available so
observability does not require the privileged surface.

The current `ExecuteMotion` command schema has no holder field. Even with the
gate enabled, an arm command over DDS therefore fails closed with
`NOT_LEASE_HOLDER`; HTTP `/v1/motion/*` (which carries `holder`) is the usable
motion entry. Adding a holder to the DDS contract requires an interfaces
proposal.

## 7. Stop responsibilities

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

## 8. Tests (all offline)

| File | Focus |
|---|---|
| `tests/test_hardware_authority.py` | no lease, missing/wrong holder, holder opt-out, scope, expiry/revoke, deadman, contract, unknown state/kind, base limits, stop coupling |
| `tests/test_lease_holder.py` | default deadman + opt-out flag, gateway default, missing/wrong holder, renew/release holder, unknown/expired/revoked, deadman heartbeat, revoke hook, HTTP holder, stop reachability |
| `tests/test_base_authority.py` | full base state machine and fault matrix, shared-safety tick latch/fault, concurrency, restart, link loss, legacy intent |
| `tests/test_watchdog.py` | trip latch, blocked upper loop, gateway-exit heartbeat loss, callback failure, base zero output |
| `tests/test_dds_gate.py` | default deny, opt-in/domain/SROS2/topic, privileged-service surface, node wiring order |
| `tests/test_base_intake.py` | exact mobile envelope accepted, flat/legacy rejected, unified gateway lease view, separate-manager lease rejected, reject-all default, lease/holder/scope, stamp freshness, seq, disabled/domain, stop priority, bad JSON, refusing sink, node default-off |

No test starts ROS, DDS, a device, or a robot.

## 9. Unverified / not covered

- The 200 ms base intent timeout, 300 ms lease deadman default and the
  mecanum mixer are design values, not measured safety limits. Deadman is
  required by default but the 300 ms window itself remains unvalidated.
- Mobile `stamp_ns` and the system receive clock are assumed to be on the same
  host and monotonic domain; cross-host delivery is not supported.
- The deadman opt-out (`allow_deadman_opt_out`) and holder opt-out
  (`require_holder=False`) are deliberate legacy/test flags; enabling them
  weakens the hardened defaults and requires review.
- Arm hold at the last measured pulse and base zero-duty semantics are not
  confirmed on the vendor/motor board.
- No hardware or bench validation, no stop-latency measurement, no physical
  E-stop integration.
- `VendorBridge` has no base motor-speed output yet; `submit_base` fails closed
  (`HARDWARE_UNAVAILABLE`) if the bridge lacks `publish_motor_speeds`.
- The `jcheng_base` intake node is wired to ROS but disabled by default. Its
  envelope is repo-local/test-only (not an interfaces contract). The unified
  lease is enforced in-process only; cross-process lease sharing is a
  deployment decision and is not implemented, so the standalone node defaults
  to `lease_source=none` and rejects everything. No base motor-speed bridge
  exists, so the node's sink refuses every desired write.
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

## 10. Rollout and rollback

- Base intake: default `enabled=False`; no launch file enables it. Enabling it
  requires an explicit config change, a reviewed base bridge, and a lease
  sharing decision.
- DDS action: default denied; enabling requires the full env/SROS2/domain
  combination above.
- HTTP lease issuing defaults to deadman-required; clients cannot opt out
  without a server-side `allow_deadman_opt_out` flag.
- Motion commands must carry `holder`; renew/DELETE must send
  `holder`/`client_id`. These are breaking client changes.
- Rollback is a code revert; no data or contract migration is involved.
