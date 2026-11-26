# Teleop six-joint L3 — PiPER X mock (domain 42 / 43)

> Status: **Partially verified (L3 simulation, 2026-11-26)**. Mock plant only,
> six revolute joints; profile v1 is not canonical for a CAN arm. No hardware,
> no vendor driver, no CAN session.

## Setup

```bash
# plant (domain 43). The launch validates the asset bundle even for the mock
# backend, so JCHENG_ASSET_ROOT must point at the pinned piper_x prefix.
cd jcheng-simulation
source /opt/ros/jazzy/setup.bash && source install/setup.bash
JCHENG_ASSET_ROOT=$HOME/.local/share/jcheng/assets \
  ROS_DOMAIN_ID=43 ros2 launch jcheng_simulation_robots robot.launch.py \
    robot:=piper_x backend:=mock

# runtime (domain 42, bridge in a second context on 43)
cd jcheng-system
source /opt/ros/jazzy/setup.bash
source ../jcheng-interfaces/install/setup.bash && source install/setup.bash
ROS_DOMAIN_ID=42 JCHENG_ROOT=$PWD ros2 run jcheng_runtime jcheng_runtime \
    --ros-args -p robot:=piper_x -p sim_bridge:=true

# probe (domain 42, joint state read in a second context on 43)
ROS_DOMAIN_ID=42 python3 tools/teleop_sim_probe.py \
    --joints 6 --velocity 0.5,0.5,-0.5,0.5,-0.5,0.5 --sim-domain 43
```

The system profile is `jcheng-system/configs/robots/piper_x/profile.yaml`
(no `servo_id`, gripper, workspace or table). `/sim/joint_states` had exactly
one publisher (`joint1…joint6`, `gripper`) before the probe started.

## Result

```text
arm True armed
lease True lease-17ece500ac1e OK
initial_positions [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
sim_positions [0.13792335149573773, 0.13792335149573773, -0.13792335149573773,
               0.13792335149573773, -0.13792335149573773, 0.13792335149573773,
               0.0]
```

All six joints moved ±0.1379 rad through `TeleopIntake` → `MotionArbiter` →
domain-43 bridge. The gripper stayed at 0 and is not commanded by this profile.
The probe released the lease on exit.

## Findings

- `servo_id` had to become optional in `RobotProfile` for a six-joint profile
  without bus-servo ids; source and the rebuilt install were both checked.
- Joint 2 (`0.0 … π`) and joint 3 (`-π … 0`) only accept one velocity sign from
  zero; the chosen pattern keeps every target inside its limit, and the intake
  clamps otherwise.
- `JCHENG_ASSET_ROOT` is validated at launch even when `backend:=mock`; without
  it the launch fails closed before starting the plant.
- PiPER X is still **not** a canonical profile: `robot-profile.v1` cannot
  express a CAN/prismatic arm, there is no hardware path, and no CAN read has
  established whether vendor feedback is a measurement or a command cache.

## Limits

- Simulation-only, six revolute joints; no gripper control.
- Not a hardware, CAN, `ExecuteMotion` or MoveIt validation.
- Mock plant lag is uncharacterized; no tracking bound is claimed.
