# Concurrency probe C1–C5 — 2026-11-26

> Status: **P0 architecture verification, FAILED on C2**. Mock only.
> No hardware was opened, contacted or energized. The gateway is the real
> `jcheng_system_core` gateway application on `ThreadingHTTPServer`; the
> backend is `MockStack` + `MockHardwareBridge`.

## 1. Method

`tools/concurrency_probe.py` builds one fresh `MockStack` per probe, arms it,
acquires a lease, starts `GatewayServer`, and drives it over loopback HTTP.
`MockHardwareBridge.publish` applies the target immediately and `bus_read`
returns at once, so the product mock has **no in-flight window**. The probe
wraps the bridge in `TimedBridge`, which sleeps in `bus_read` for 500 ms
(30 ms in the stress probe). That delay is **harness-only**; it is not a change
to the product mock and it is not a hardware model.

Reproduce:

```bash
python3 tools/concurrency_probe.py --out /tmp/probe.json
```

Raw report: `probe.json`.

## 2. Results

| ID | Probe | Verdict | Key observation |
|---|---|---|---|
| C1 | stop while idle | PASS | 200 in 0.9 ms; stop latched |
| C2 | stop while execute is in flight | **FAIL** | execute still returned `COMPLETED` after the stop |
| C3 | second execute while one is in flight | PASS | `REJECTED` / `MOTION_BUSY`, one backend command |
| C4 | client drops before the response; idempotent replay | PASS | replay returned the cached result; one backend command |
| C5 | lease revoked during execute | **FAIL** | `COMPLETED`, safety `ARMED_IDLE`, not latched |
| C5b | lease expired during execute | **FAIL** | `COMPLETED`, safety `ARMED_IDLE`, not latched |
| C2-stress | stop/execute race, 30 iterations | **FAIL** | 30/30 double terminal, 30/30 `COMPLETED` after stop |

## 3. Findings

**F1 — two terminal results for one intent (C2, C2-stress).**
`MotionArbiter.stop()` completes the active motion as `ABORTED` and clears
`_active`. The concurrent `execute()` captured `_active` at the top of
`finish()` before its `bus_read`, so it continues and completes the same intent
as `COMPLETED` after `safety.end_motion()`. Both calls write the idempotency
cache; the last writer wins, so the cached result is `COMPLETED`.

Observed in C2:

```text
terminal_completions: {"ABORTED": 1, "COMPLETED": 1}
final_cached_outcome: "COMPLETED"
execute_outcome: "COMPLETED"
execute_safety_state: "STOP_LATCHED"
```

The execute response is HTTP 200 with `outcome=COMPLETED` while its own
`SafetyState` says `STOP_LATCHED`. A client that trusts the outcome over the
safety field records a successful motion after a stop.

**F2 — lease loss during a motion is not enforced (C5, C5b).**
`finish()` never re-checks the lease, and the gateway process never calls
`MotionArbiter.tick()` (`gateway.py` `main()` only sleeps). Lease revocation and
expiry therefore have no effect on an in-flight motion; it completes as
`COMPLETED`.

**F3 — active-motion state is unsynchronized.**
`_active` and `_idempotency` (`motion.py`) are plain attributes mutated from
concurrent `ThreadingHTTPServer` handler threads. There is no lock around the
read-modify-write sequences in `start()`, `finish()`, `stop()` and `_complete()`.
C2 is the observed consequence, not a theoretical one.

**F4 — the ROS runtime serializes cancel behind execute.**
In `jcheng_runtime/node.py`, `_execute()` holds `self._lock` around
`arbiter.execute()`, and `_cancel()` takes the same lock before calling
`arbiter.stop()`. A cancel cannot pre-empt an executing goal; it waits for the
motion to finish. This is a second, independent failure of the same requirement.

**F5 — the product mock cannot represent an in-flight motion.**
`execute()` calls `start()` and `finish()` back to back, and `bus_read` returns
immediately. C2–C5 are only observable with the harness delay. This is a
fidelity gap in the mock, separate from the race itself.

## 4. What passed

- Idle stop is fast and latches (C1).
- A second execute is rejected as `MOTION_BUSY`, not queued (C3).
- Idempotent replay after a dropped connection returns the cached result and
  does not publish a second command (C4).

## 5. Limitations

- No physical stop exists on this platform; the probe says nothing about
  physical safety. `StopResult` says so itself: "software stop requested; not
  an emergency stop".
- The settle delay is a harness model, not a vendor or hardware measurement.
- The probe exercises the jcheng-system mock gateway, not the deployed
  `armpi_debug_gateway` on port 8766 and not the frozen baseline.
- Loopback only; no DDS, no device, no SSH.

## 6. Consequence

Per the operator decision of 2026-11-26, **C2 failing pauses M3** in
`jcheng-applications`. The system-side execution model revision is proposed in
[`docs/EXECUTION-MODEL-REVISION.md`](../../../docs/EXECUTION-MODEL-REVISION.md).
No application-layer workaround is permitted.
