# Concurrency probe C1–C5, revision 2 — 2026-11-26

> Status: **PASS on the mock**. R1–R4 implemented; R5 (contract confirmation
> fields) is pending a `jcheng-interfaces` review. No hardware was touched.
> The previous failing run is preserved in
> [`../20261126_concurrency_probe/`](../20261126_concurrency_probe/README.md).

## 1. Changes under test

| Revision | Change |
|---|---|
| R1 | `MotionArbiter` session state with one `RLock`; `_complete_once` is the single terminal writer; idempotency cache written only there |
| R2 | `stop()` completes `ABORTED` but keeps `_active` until the execute worker exits; a new execute is `MOTION_BUSY` until then; `start()` publishes under the lock so a stop either precedes the publish or holds after it |
| R3 | `finish()` re-checks the lease before any `COMPLETED`; `tick()` checks lease and stop latch; `LeaseManager` is locked and distinguishes `LEASE_REVOKED` from `LEASE_EXPIRED`; `SafetySupervisor` transitions are locked; the gateway process runs a 20 Hz tick thread |
| R4 | `jcheng_runtime` no longer holds its outer lock across `execute`, `cancel`, `tick` or the safety-stop callback, so cancel and tick can reach the arbiter during a motion |
| R6 | This re-run of C1–C5 and the 30-iteration stress probe |

## 2. Results

| ID | Probe | Result | Key observation |
|---|---|---|---|
| C1 | stop while idle | PASS | 200, latch, 0.9 ms |
| C2 | stop during execute | PASS | execute 200 `ABORTED` `SAFETY_STOP_LATCHED`; **one terminal object**; cached `ABORTED` |
| C2p | stop during the command write | PASS | stop waited 0.13 s for the write then held; execute `ABORTED`; one backend command |
| C3 | second execute in flight | PASS | `REJECTED/MOTION_BUSY`; one backend command |
| C4 | dropped connection + replay | PASS | cached `COMPLETED`; one backend command |
| C5 | lease revoke during execute | PASS | 200 `ABORTED` `LEASE_REVOKED`; safety latched |
| C5b | lease expiry during execute | PASS | 200 `ABORTED` `LEASE_EXPIRED`; safety latched |
| C2-stress | 30 stop/execute races | PASS | 30/30 `ABORTED`, 0 double terminals, 0 completed-after-stop, 0 HTTP 500 |

C2p covers the write window: `stop()` latches and holds inside the session
lock, so its hold cannot run before a concurrent publish. The command write
finishes first and the hold follows; the terminal is `ABORTED`.

Raw report: [`probe.json`](probe.json). Local core tests: [`pytest.txt`](pytest.txt).

### 2.1 Measurement note

The first re-run reported `{"ABORTED": 2}` for C2. That counted **calls** to
`_complete_once` (stop and finish both call it), not terminal writes. The final
probe counts distinct terminal result objects per intent; C2 and the stress loop
now report one object per intent. `completion_calls` is retained as a separate
observation.

## 3. What is now true

- One intent has one terminal result, even when stop and finish race.
- A stop accepted before the terminal result yields `ABORTED`; a late
  `COMPLETED` is not written and cannot overwrite the idempotency cache.
- Lease revoke and expiry during a motion produce `ABORTED` with the lease code.
- A second execute is rejected while one is in flight or stopping.
- Idempotent replay still returns the cached terminal result without a second
  backend command.

## 4. What is not true yet

- The probe runs the **mock** gateway with a harness-only settle window. The
  deployed `armpi_debug_gateway` on port 8766 was not probed; M4 needs C2–C5
  there.
- R5 (`StopResult` confirmation levels S1/S2 and `physical_stop: unverified`)
  is not implemented. That is a `jcheng-interfaces` contract change; until it
  lands, the gateway must not describe an accepted stop as backend-confirmed.
- Physical stop is not measurable on this platform and is never claimed.
- The ROS runtime (`tests_ros`) was not executed locally; CI `m5-mock` in
  Humble is the authority for R4.

## 5. Reproduction

```bash
python3 tools/concurrency_probe.py --out e2e/reports/20261126_concurrency_probe_r2/probe.json
```

C1–C5 use the real gateway application and `MockStack`; the settle delay is
added by the probe, not by the product mock. No device, DDS or SSH is used.
