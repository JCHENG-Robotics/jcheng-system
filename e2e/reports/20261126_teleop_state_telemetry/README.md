# Runtime read-only state telemetry tap

> Status: **Partially verified (mock + simulation, 2026-11-26)**. Test-only tap
> from `jcheng_runtime`; no hardware, no vendor path, no production telemetry
> claim.

## Purpose

`jcheng_runtime` now publishes `RobotState` on `/jcheng/telemetry/state` at its
20 Hz tick, next to the existing `/jcheng/telemetry/safety`. Capability
consumers (teleop capture) need a labelled read-only state feed on the
capability domain; before this they had none.

This is **not** the production `jcheng_telemetry` node. That node owns the
vendor bus and `/controller_manager/joint_states` on domain 0. The runtime tap
is a test fixture publisher on the runtime's domain (42 for the canonical
launch).

## Labels

| Backend | `source` | `truth` | `verification` |
|---|---|---|---|
| mock stack | `jcheng_runtime/mock` | `commanded` | `timed` |
| simulation bridge | `jcheng_runtime/sim_bridge` | `measured` | `simulated` |

The mock bridge labels itself `measured`/`bus` internally to exercise the
safety path; that is a fixture, not a measurement claim. The tap therefore
relabels only the mock backend to `commanded`/`timed`. The simulation bridge's
label passes through unchanged: the simulator measured the joint, and
`verification=simulated` says how.

```text
# mock
commanded
timed
# simulation bridge
measured
simulated
```

## Tests

`python3 -m pytest -q tests_ros/test_teleop_runtime.py` — 5 passed. The new
tests check that the mock tap is relabelled and carries the profile/joint
state, and that a simulation-labelled `RobotState` passes through
`state_to_msg` unchanged.

## Consumer evidence

The teleop capture harness records these messages as observation rows:

- `jcheng-teleop` evidence `evidence/20261126_m3_capture/`:
  - mock capture: 252 observation rows, `commanded`/`timed`;
  - simulation capture: 188 observation rows, `measured`/`simulated`,
    joint1 0 → 0.1361 rad.

## Limits

- Test-only; not a deployment component and not the production telemetry node.
- No authentication or authorization on the topic; it is read-only by
  convention and contains no credentials.
- The same topic name is used by the production telemetry node on domain 0;
  the two graphs are separate. A future single-domain deployment must not run
  both publishers.
- No hardware validation; the tap has only been exercised with `MockStack` and
  `RosSimHardwareBridge`.
