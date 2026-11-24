# jcheng-system roadmap

> Status: **v0.3.0-dev** as of 2026-11-24. The system runtime has passed M4
> read-only and the core of M5.2; M6 execution remains.

## Current state

| Item | State | Evidence |
|---|---|---|
| M3 mock core and S1–S17 | Complete (2026-11-23) | `e2e/reports/m3-mock.json` |
| M4 read-only telemetry | Complete (2026-11-23) | 1799/1799 `measured`, `writes=0` |
| M5.1 mock ROS transport | Complete (2026-11-24) | 5 `tests_ros`; CI `m5-mock` |
| M5.2 supervised joint/gripper | Complete, core (2026-11-24) | joint1 505 vs 512; gripper 183→645 / 647→183 |
| M6 S13 replay | Complete (2026-11-24) | 21/21 decisions |
| M6 S17 plan-only | Complete, offline (2026-11-24) | `test_moveit_plan_only.py` |
| M6 real MoveIt / S14 base / full G8 | Not started | — |
| S7 stale-state injection, S9 runtime publisher | Not verified | — |

## Milestones

| Milestone | Scope | State | Exit criterion | Evidence |
|---|---|---|---|---|
| M1 | Repository skeleton and scope | Complete (2026-11-23) | Skeleton only | `docs/M1-SCOPE.md` |
| M3 | Mock stack, 9 packages, S1–S17, lockfile | Complete (2026-11-23) | Required failures = 0 | `docs/M3-SCOPE.md` |
| M4 | Read-only bridge, gateway, domain config | Complete (2026-11-23) | 30 min, `writes=0` | `docs/M4-SCOPE.md` |
| M5.1 | Mock ROS services/actions, lease, safety, stop latch | Complete (2026-11-24) | 5 ROS tests; `m5-mock` | `docs/M5-SCOPE.md` |
| M5.2 | Supervised hardware joint and gripper | Complete, core (2026-11-24) | `COMPLETED` + `bus_verified`; stop latches | `docs/M5-SCOPE.md` |
| M6 | Replay and MoveIt plan-only | In progress | S13 21/21; S17 offline; real MoveIt/S14/G8 | `docs/M6-SCOPE.md` |
| M7 | Integrated capability through the system API | Planned | A capability passes L3 and supervised L5 | — |
| M8 | System v1.0 with safety case and lockfile freeze | Planned | All gates green; no open residual | — |

## Next

1. **Finish M5.2 residuals.** Run S7 stale-state injection and S9 runtime
   publisher detection in a supervised hardware session.
2. **Finish M6.** Real MoveIt plan-only (S17), S14 hardware base, full G8
   re-run and landing evidence. Prerequisites: separate explicit
   authorization, operator present.
3. **Domain bridge.** Implement the vendor-domain → 41/42 bridge required
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
