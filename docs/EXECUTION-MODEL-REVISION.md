# Execution model revision — proposal

> Status: **Implemented (R1–R4), verified on the mock (R6)** as of 2026-11-26.
> R5 (stop confirmation fields) is **filed as a proposal** in
> `jcheng-interfaces/docs/proposals/stop-confirmation-levels.md`; the schema
> review is pending and it is **not** implemented.
> Trigger: P0 concurrency probe C2 failed. Evidence:
> [`e2e/reports/20261126_concurrency_probe/`](../e2e/reports/20261126_concurrency_probe/README.md).
> Re-run evidence:
> [`e2e/reports/20261126_concurrency_probe_r2/`](../e2e/reports/20261126_concurrency_probe_r2/README.md).
> The mock probes are green; the deployed gateway was not probed and M4 still
> requires C2–C5 there. No motion is authorized by this document.

## 1. Problem

The gateway's `execute` path is a synchronous read-modify-write over shared
mutable state, reached from concurrent `ThreadingHTTPServer` handler threads.
A stop that arrives while an execute is in flight produces two terminal
results for one intent: `ABORTED` from `stop()` and `COMPLETED` from
`finish()`. The idempotency cache keeps the last writer, so a replay reports
`COMPLETED`. Lease revocation and expiry are not checked during the motion at
all.

The probe reproduced this 30/30 times with a 30 ms settle window. It is not a
rare interleaving.

### 1.1 Observed facts (code references)

| Fact | Location |
|---|---|
| One handler thread per request | `gateway.py`, `ThreadingHTTPServer` |
| `finish()` captures `_active` before a blocking `bus_read()` | `motion.py` `finish()` |
| `stop()` completes and clears `_active` independently | `motion.py` `stop()` |
| `_complete()` overwrites `_idempotency[key]` (last writer wins) | `motion.py` `_complete()` |
| `_active` and `_idempotency` are unguarded | `motion.py` `__init__` |
| The gateway process never calls `arbiter.tick()` | `gateway.py` `main()` |
| The ROS runtime holds one lock across `execute`, and `_cancel` takes the same lock | `jcheng_runtime/node.py` `_execute`, `_cancel` |

### 1.2 Requirements the revision must satisfy

1. Exactly one terminal `MotionResult` per intent, ever.
2. A stop that is accepted before the terminal result must produce `ABORTED`,
   never `COMPLETED`, and must not be overwritten by a late completion.
3. Lease loss (revoke or expiry) during a motion produces `ABORTED` with the
   lease reason.
4. No new motion starts while a previous session is stopping or unsettled.
5. A terminal result must never say `COMPLETED` while `SafetyState.stop_latched`
   is true.
6. The HTTP contract stays terminal-result-per-request; the caller does not
   need a new async protocol.
7. HTTP 200 is **not** physical safety evidence. The stop response must state
   which confirmation level it reached.

### 1.3 Revision 2 result (2026-11-26)

| Probe | Result |
|---|---|
| C1 idle stop | PASS |
| C2 stop during execute | PASS: one terminal object, `ABORTED`, cached `ABORTED` |
| C2p stop during the command write | PASS: stop waits for the write, then holds; `ABORTED` |
| C3 second execute | PASS: `REJECTED/MOTION_BUSY` |
| C4 dropped connection + replay | PASS: cached result, one backend command |
| C5 lease revoke during execute | PASS: `ABORTED/LEASE_REVOKED` |
| C5b lease expiry during execute | PASS: `ABORTED/LEASE_EXPIRED` |
| C2 stress, 30 iterations | PASS: 0 double terminals, 0 completed-after-stop, 0 HTTP 500 |

CI run 45748815799 on the merged commit `2ffde33` passed `m1-gate`,
`m3-mock`, `m4-readonly`, `m5-mock`, `m5-vendor` and `m6-replay`; R4's ROS
runtime check is therefore covered by `m5-mock`.

The deployed `armpi_debug_gateway` is a different implementation and was not
probed. M3 is unblocked on the mock; M4 requires the same probes on the
deployed gateway.

## 2. Proposed model

### 2.1 Session state machine, single writer

```text
ACCEPTED ──► EXECUTING ──► SETTLING ──► TERMINAL
                 │             │
                 └── stop ─────┴──► STOPPING ──► TERMINAL (ABORTED)
```

- One `MotionSession` per intent, held in `_active`.
- One `threading.RLock` guards session transitions, `_active` and
  `_idempotency`. It is never held across `bus_read`, a transport wait or any
  sleep. It **is** held across the command write and the stop hold, so a
  publish and a hold have a total order; the hold can never precede the publish
  it is meant to stop.
- `_terminal` is written exactly once by `_complete_once(session, result)`.
  The first caller wins; every later caller receives the stored result.
- The idempotency cache is written only by `_complete_once`.
- A `COMPLETED` result is refused structurally if `stop_latched` is true: the
  transition converts it to `ABORTED` with `SAFETY_STOP_LATCHED`.

### 2.2 Execute

```text
execute(command):
  with lock:
    replay = idempotency.get(key)
    if replay: return replay                  # terminal only
    if active is not None: return REJECTED(MOTION_BUSY)
    report = validate(); if not approved: return REJECTED
    session = create(ACCEPTED, cancel_event, future)
    active = session
  worker(session):                             # one per session
    publish(command)
    transition(EXECUTING)
    wait for settle, cancel or lease tick
    measured = bus_read()
    complete_once(session, COMPLETED or ABORTED)
    future.set_result(result)
  return future.result(timeout=profile.max_duration + margin)
```

- The HTTP handler waits on a future, so the request still returns a terminal
  result.
- A worker that exceeds the profile bound produces `UNKNOWN` and leaves the
  session `STOPPING`; a new process start is required to clear a truly wedged
  worker. The tick thread still runs the safety and lease checks.
- A replay of an in-flight key returns `REJECTED(MOTION_BUSY)` or waits for
  the future; it never starts a second publish. Choose one and document it in
  the contract tests. Recommendation: wait up to a short bound, then
  `MOTION_BUSY`.

**Implementation note (R1–R3, 2026-11-26).** The shipped version keeps
`execute()` synchronous in the calling thread and uses the session lock for
transitions; it does not start a per-session worker or return a future. The
invariants above are unchanged: one terminal writer, stop wins, lose lease
aborts. A worker/future form is only needed if the HTTP request must return
before the terminal result, which is not the current contract.

### 2.3 Stop

```text
stop(operator, reason):
  with lock:
    safety.latch(SAFETY_STOP_LATCHED)
    hardware.hold(); hardware.zero_base()
    session = active
    if session is None or session.terminal: return StopResult(backend_confirmed)
    session.cancel_event.set()
    session.state = STOPPING
    complete_once(session, ABORTED, SAFETY_STOP_LATCHED)
    terminal = session.terminal
  return StopResult(terminal, confirmation="backend_confirmed")
```

- The latch and hold are inside the same session lock as `start()`'s publish.
  A concurrent start therefore either sees the latch and does not publish, or
  publishes first and the hold runs after the command write. The hold can never
  precede a publish that it was meant to stop.
- `stop()` does not wait for the worker and does not clear `_active`. The
  worker clears it on exit; until then a new `execute` is `MOTION_BUSY`.
- The worker's late `bus_read` result is recorded as a late measurement in
  evidence, never as the terminal outcome.
- `stop()` when idle still latches and is idempotent.

### 2.4 Lease and time

- A gateway **tick thread** runs at 20 Hz: `authority.expire(now)`,
  `safety.tick(now)`, `hardware.tick(now)`, `arbiter.tick(now)`.
- `arbiter.tick()` on an active session checks the lease and the stop latch;
  on failure it enters the stop path and completes `ABORTED` with the lease
  code (`LEASE_REVOKED`, `LEASE_EXPIRED`, `LEASE_PREEMPTED`).
- The same check runs before `complete_once(COMPLETED)`, so a lease that is
  already invalid at completion cannot yield `COMPLETED`.
- The revision removes `arbiter.tick()` from "nobody calls it" to a documented
  process responsibility, shared by the HTTP gateway and the ROS runtime.

### 2.5 ROS runtime

- `_execute()` must not hold `self._lock` across `arbiter.execute()`.
- `_cancel()` calls the same `arbiter.stop()` path and returns `ACCEPT` without
  waiting for the execute goal.
- The action goal aborts with the session's terminal result; the result is not
  produced a second time by the runtime.

## 3. Stop confirmation levels

The revision separates three levels that are currently collapsed into
"HTTP 200":

| Level | Meaning | Evidence |
|---|---|---|
| S1 request accepted | The gateway accepted `/v1/motion/stop` and latched safety | HTTP 200 + `StopResult` |
| S2 backend stop confirmed | The gateway's hold/zero command returned without error and the session is terminal `ABORTED`; no further motion will be issued by the gateway | `StopResult.confirmation = "backend_confirmed"` |
| S3 physical stop | The arm or base actually stopped | **Not measurable on this platform.** No e-stop, brake, encoder or force sensing. Never claimed. |

`StopResult` should carry `confirmation` and an explicit
`physical_stop: "unverified"`. That is a `jcheng-interfaces` change, so it needs
a schema PR and a contract bump before implementation. Until then, the gateway
must not describe an accepted stop as a confirmed stop in prose or in the app.

## 4. Mock fidelity

The product mock completes `execute` in microseconds, so C2 is unobservable
without the harness delay (finding F5). Proposed:

- `MockHardwareBridge` gains an explicit `settle_s` (default `0.0`, unchanged
  behavior) and `hold_freezes` semantics, so a scripted motion has a real
  in-flight window.
- The settle model is a test property, not a hardware claim. It is recorded in
  the scenario manifest.
- The mock's `hold()` should freeze at a modelled intermediate position rather
  than only zeroing the base; otherwise "stop mid-motion" is untestable.

## 5. Migration and gates

| Step | Change | Gate | Status |
|---|---|---|---|
| R1 | Session state machine, one lock, `_complete_once`, locked idempotency | C2 single-shot PASS; C2-stress 0/30 double | **Done** (mock, 2026-11-26) |
| R2 | Stop path with `STOPPING` and `MOTION_BUSY` until the worker exits | C2-stress 0 completed-after-stop, 0 500 | **Done** (mock, 2026-11-26) |
| R3 | Tick thread and lease checks before completion | C5, C5b PASS | **Done** (mock, 2026-11-26) |
| R4 | ROS runtime cancel path; no lock across execute/tick | Same probes through the runtime adapter | **Done**; `m5-mock` green in CI run 45748815799 |
| R5 | `StopResult` confirmation fields (contract proposal) | Interfaces review | **Proposal filed** 2026-11-26; schema review pending; not implemented |
| R6 | Re-run C1–C5 on the mock and on the deployed gateway model | M3 unblock | **Mock done**; deployed gateway pending for M4 |

C1 (idle stop), C3 (busy rejection) and C4 (idempotent replay) already pass and
must remain green.

## 6. Impact on `jcheng-applications`

- M3 is **paused** until R6. Its supervisor design stays as specified in
  `jcheng-applications/docs/TASK_ENGINE.md`; the app-side stop-race tests are
  not a substitute for a correct gateway.
- No application-layer workaround (retry suppression, outcome rewriting,
  "ignore COMPLETED after stop") is permitted. Those would hide the defect and
  make the run record false.
- The application must treat S1 and S2 as distinct and must never present
  either as S3.

## 7. Open questions

1. Which implementation is the M6 target: this `jcheng_system_core` gateway, or
   the deployed `armpi_debug_gateway` on port 8766? The probe covers only the
   former.
2. Should a replay of an in-flight idempotency key wait or fail fast? The
   contract must say.
3. Is a gateway tick thread acceptable alongside the ROS executor's own tick,
   or should the runtime own time and the gateway delegate?
4. Does `StopResult.confirmation` belong in the v1 contract as an additive
   field, or as a v0.2 contract?
5. What is the maximum accepted execute request duration, and what does the
   client do on a 504/`UNKNOWN` while the session is still stopping?
