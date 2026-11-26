# Piper X — upstream pointers (not implemented)

> Status: **pointer only**, 2026-11-26. No bridge, config, IDL or motion path is
> added by this document. Motion remains `ExecuteMotion`-only; the vendor
> driver is not a JCHENG component.
> English canonical.

## 1. What exists upstream

| Source | Commit | Licence | What it provides |
|---|---|---|---|
| `agilexrobotics/agx_arm_ros` (`ros2`) | `747d0722074d4cebc095d8d44f3ef68d2023ba6f` | MIT | ROS 2 driver (`arm_type:=piper_x`), MoveIt2/SRDF/ros2_control, CAN and TCP docs |
| `agilexrobotics/pyAgxArm` | `d90e234ba8e408cfa964b38aa9120b3131fc9f85` | **LGPL-3.0** | Python SDK used by the driver; `ArmModel.PIPER_X`, CAN protocol, joint-limit/MDH constants |
| `agilexrobotics/piper_sdk` | `b847e5393a7ce6ff8508d713eca7bdd3a42421b4` | MIT | Legacy Python CAN SDK; README points new work at `pyAgxArm` |

The vendor driver publishes `feedback/joint_states`, `feedback/tcp_pose`,
`feedback/arm_status`, etc., and subscribes to `control/joint_states`,
`control/move_j`, `control/move_p`, `control/move_c`, `control/move_js`,
`control/move_cpv`, `control/move_mit`; it also exposes `enable_agx_arm`,
`control_enable`, `move_home`, `emergency_stop` and `exit_teach_mode` services.
These names are **vendor interfaces**, not JCHENG contracts.

## 2. Why there is no bridge yet

- `HardwareBridge` in `jcheng_vendor/jcheng_vendor/executor.py` is a
  pulse/bus-servo interface (`send_step`, `send_gripper`, `hold`, `read_bus`,
  `pulse_snapshot`). A CAN arm needs a different implementation and a separate
  review; it is not a drop-in adapter.
- Piper X has no servo IDs or pulse telemetry. `robot-profile.v1` requires
  `servo_id` and measured pulse arrays, so a v0.2 CAN profile is a contract
  change; see the proposal in `jcheng-interfaces`.
- The vendor `emergency_stop` service is a software stop. The platform has no
  e-stop, brake, independent encoder or self-collision model; no document may
  call the vendor service an e-stop.
- `/joint_states` from the vendor stack is a command cache in this ecosystem's
  safety model; completion and pose truth must come from a bus read, and the
  read path has no safe timeout. Do not design a completion check around the
  cached topic.
- The arm must not join domain 0/42 directly or publish vendor topics into the
  privileged graph. A future bridge belongs on domain 41 behind
  `ExecuteMotion`, with lease, deadman, limits and the stop latch applied by the
  system layer.

## 3. What is already placed

- Source audit and licence matrix:
  [`piper-x-assets.md`](../../../jcheng-research/references/manipulation/piper-x-assets.md)
- Data map and decision record:
  [`20261126-piper-x-simulation-data-map.md`](../../../jcheng-research/reports/20261126-piper-x-simulation-data-map.md)
- Simulation asset manifest, joint table and fetch script:
  [`jcheng-simulation/docs/robots/piper_x/`](../../../jcheng-simulation/docs/robots/piper_x/README.md)
- CAN profile proposal (not a contract change):
  [`piper-x-robot-profile.md`](../../../jcheng-interfaces/docs/proposals/piper-x-robot-profile.md)
- Pinned binaries: `~/.local/share/jcheng/assets/piper_x/` (outside git)

## 4. Preconditions before implementation

1. Explicit, per-session hardware authorization; no motion from CI, scripts or
   unattended runs.
2. A read-only CAN session to establish whether the SDK feedback is a bus
   measurement or a command cache, and to confirm joint signs/index order.
3. Firmware confirmation of the J6 limit: the MIT URDF and `pyAgxArm` say
   ±170°, the public specification says ±165°.
4. Measured TCP (`link6` → gripper fingertip); the 0.1425 m value in the vendor
   TCP document is a standard-Piper example, not Piper X.
5. A safety review that covers the new bridge, lease/deadman behaviour, limit
   source, stop semantics and the LGPL obligations of `pyAgxArm`.

Until these are closed, Piper X remains a simulation/reference target, not a
deployed arm.
