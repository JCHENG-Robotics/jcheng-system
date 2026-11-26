# jcheng-system roadmap

> Status: **v0.3.0-dev** as of 2026-11-25. M4 read-only, M5.2 core motion and
> the M6 S13 replay plus S17 real MoveIt plan-only are verified; S14 base,
> full G8 and the S7/S9 residuals remain.

## Current state

| Item | State | Evidence |
|---|---|---|
| M3 mock core and S1–S17 | Complete (2026-11-23) | `e2e/reports/m3-mock.json` |
| M4 read-only telemetry | Complete (2026-11-23) | 1799/1799 `measured`, `writes=0` |
| M5.1 mock ROS transport | Complete (2026-11-24) | 5 `tests_ros`; CI `m5-mock` |
| M5.2 supervised joint/gripper | Complete, core (2026-11-24) | joint1 505 vs 512; gripper 183→645 / 647→183 |
| M6 S13 replay | Complete (2026-11-24) | 21/21 decisions; validation latency mean 0.0166 ms, p95 ≤0.0352 ms |
| M6 S17 plan-only | Complete, offline (2026-11-24) | `test_moveit_plan_only.py` |
| M6 S17 real MoveIt plan-only | Complete (2026-11-25) | `SUCCESS`, 8-point plan, 0 controller servers, no command topics; `docs/M6-SCOPE.md` |
| ArmPi Ultra runtime image | Complete (2026-11-25) | `armpi-ultra:flatten-20261125`, 7.1 GB; `deploy/armpi-ultra/docs/ACCEPTANCE-20261125.md` |
| M6 S14 base / full G8 | Not started | — |
| Teleoperation intake core (mock) | Complete, mock only (2026-11-26) | `tests/test_teleop.py`; `docs/TELEOP-INTAKE.md` |
| S7 stale-state injection, S9 runtime publisher | Not verified | — |

### P0 execution-model blocker — resolved on the mock (2026-11-26)

The original probe failed C2: a stop during an in-flight execute produced two
terminal results and a late `COMPLETED`, and lease loss during a motion was not
enforced. After R1–R4 (single terminal writer, stop path, lease checks, tick
thread, runtime cancel path), the re-run passes C1–C5, C5b and a 30-iteration
stop/execute stress loop with zero double terminals. Evidence:
[`e2e/reports/20261126_concurrency_probe_r2/`](e2e/reports/20261126_concurrency_probe_r2/README.md).
Original failure:
[`e2e/reports/20261126_concurrency_probe/`](e2e/reports/20261126_concurrency_probe/README.md).
`jcheng-applications` M3 is **unblocked** on the mock. CI run
[45748815799](https://github.com/JCHENG-Robotics/jcheng-system/actions/runs/45748815799)
passed `m1-gate`, `m3-mock`, `m4-readonly`, `m5-mock`, `m5-vendor` and
`m6-replay` on the merged commit.

Still open: R5 (`StopResult` confirmation levels) needs a `jcheng-interfaces`
review; the deployed `armpi_debug_gateway` was not probed, and M4 must repeat
C1–C5 there before any hardware session.

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M1 | Repository skeleton and scope | Complete (2026-11-23) | Skeleton only | `docs/M1-SCOPE.md` |
| M3 | Mock stack, 9 packages, S1–S17, lockfile | Complete (2026-11-23) | Required failures = 0 | `docs/M3-SCOPE.md` |
| M4 | Read-only bridge, gateway, domain config | Complete (2026-11-23) | 30 min, `writes=0` | `docs/M4-SCOPE.md` |
| M5.1 | Mock ROS services/actions, lease, safety, stop latch | Complete (2026-11-24) | 5 ROS tests; `m5-mock` | `docs/M5-SCOPE.md` |
| M5.2 | Supervised hardware joint and gripper | Complete, core (2026-11-24) | `COMPLETED` + `bus_verified`; stop latches | `docs/M5-SCOPE.md` |
| M6 | Replay and MoveIt plan-only | In progress — S13 21/21 and S17 real pass; S14 base and full G8 pending | `docs/M6-SCOPE.md` |
| M7 | Integrated capability through the system API | Planned | A capability passes L3 and supervised L5 | — |
| M8 | System v1.0 with safety case and lockfile freeze | Planned | All gates green; no open residual | — |

## Next

1. **Finish M5.2 residuals.** Run S7 stale-state injection and S9 runtime
   publisher detection in a supervised hardware session.
2. **Finish M6.** S17 real MoveIt plan-only passed on 2026-11-25; S14 hardware
   base, full G8 re-run and landing evidence remain. Prerequisites: separate
   explicit authorization, operator present.
3. **Maintain the published image.** `deploy/armpi-ultra/container/image.lock`
   records the 2026-11-25 `armpi-ultra:flatten-20261125` digest; re-record it on
   every image change.
4. **Domain bridge.** Implement the vendor-domain → 41/42 bridge required
   before a capability module can consume telemetry without joining domain 0.

## Out of scope

- Runtime dependency on L3 capabilities or L4 applications.
- A second hardware publisher outside the bridge.
- Automatic restart of the vendor container; the boot sequence moves the arm.
- Functional-safety certification; no e-stop, brake, independent encoder or
  self-collision model.

## Dependencies

- `jcheng-interfaces` v0.1.0, contract hash
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`.
- `locks/ecosystem.lock.yaml` pins interfaces, contract hash and profile hash.
- Every L5 gate requires separate, explicit operator authorization.
