# Teleop -> system intake -> simulation bridge (L3, Gate-2)

> Status: **Partially verified (L3 simulation, 2026-11-26)**. Domain 43 only;
> test-only bridge. No hardware.

## Setup

- `jcheng-simulation` Gate-2 mock plant (2 joints) on domain 43.
- `jcheng_runtime` with `sim_bridge:=true` and `robot:=gate2_arm` on domain 43.
- `tools/teleop_sim_probe.py` adapted to two joint velocities; arms the
  system, acquires a priority-40 arm lease, publishes a short 50 Hz
  `TeleopCommand` script, sends a neutral frame and prints `/sim/joint_states`.
- The Gate-2 system profile has no gripper/workspace/table; POSE/GRIPPER
  commands are rejected, velocity steps are allowed.

## Result

```text
arm True armed
lease True lease-c88774615b19 OK
sim_positions [0.5013147014435476, -0.5013147014435476]
```

Both joints moved from 0 to about ±0.501 rad. The probe released the lease on
exit.

## Limits

- Gate-2 is a simulation/test profile only (`servo_id` placeholders); not a
  hardware claim.
- PiPER X is still blocked by the v1 profile schema (CAN/prismatic arm).
- Domain 43 test harness, not the canonical domain-42 deployment.
- No hardware or vendor executor.
