# Teleop -> system intake -> simulation bridge (L3, ArmPi)

> Status: **Partially verified (L3 simulation, 2026-11-26)**. Domain 43 only;
> test-only bridge. No hardware, no vendor command and no domain 0/41/42.

## Setup

- `jcheng-simulation` ArmPi mock plant on domain 43.
- `jcheng_runtime` with `sim_bridge:=true` on domain 43; `RosSimHardwareBridge`
  publishes `/sim/joint_command` and reads `/sim/joint_states` with best-effort
  depth-1 QoS.
- `tools/teleop_sim_probe.py` arms the system, acquires a priority-40 arm lease,
  publishes a short `TeleopCommand` velocity script at 50 Hz, sends a neutral
  frame and prints `/sim/joint_states`.

## Result

```text
arm True armed
lease True lease-49b5ba36fa06 OK
sim_positions [0.2049832552584121, 0.0, 0.0, 0.0, 0.0, -0.4]
```

The first arm joint moved from 0 to about 0.205 rad; the gripper stayed at its
initial -0.4 rad. The probe released the lease on exit.

## Limits

- ArmPi profile only; Gate-2 and PiPER X profiles are not wired into the
  system runtime yet.
- Domain 43 test harness, not the canonical domain-42 deployment.
- The arbiter's `bus_read` sees the simulation state at the step boundary; the
  plant's first-order tracking lag is not yet characterized as a bound.
- No hardware claim.
