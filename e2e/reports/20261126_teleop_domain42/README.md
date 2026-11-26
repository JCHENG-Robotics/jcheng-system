# Teleop canonical-domain L3 (Gate-2)

> Status: **Partially verified (L3, 2026-11-26)**. Teleop/runtime on domain 42;
> simulation bridge on a separate domain-43 context. Test-only bridge; no
> hardware.

## Setup

- Gate-2 mock plant on domain 43.
- `jcheng_runtime` on domain **42** with `sim_bridge:=true`,
  `robot:=gate2_arm`. `RosSimHardwareBridge` runs in a second `rclpy` context
  on domain 43, so the runtime and teleop publisher stay on the canonical
  capability domain.
- Teleop probe on domain 42 arms the system, acquires a priority-40 arm lease,
  publishes two-joint velocity at 50 Hz and sends a neutral frame.
- `/sim/joint_states` is read on domain 43.

## Result

```text
arm True armed
lease True lease-e19205dd64c4 OK
sim_positions [0.12491019028688566, -0.12491019028688566]
```

Both joints moved from 0 to about ±0.125 rad. The probe released the lease on
exit.

## Limits

- Gate-2 test profile only (`servo_id` placeholders); no hardware claim.
- PiPER X remains blocked by the v1 profile schema.
- The bridge is test-only; it is not a deployment component.
- No vendor executor, bus read, e-stop or physical stop confirmation.
