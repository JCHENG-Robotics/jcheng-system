# Teleop canonical domain 42 — ArmPi (L3)

> Status: **Partially verified (L3 simulation, 2026-11-26)**. Teleop/runtime on
> domain 42; simulation bridge and plant on domain 43. Test-only bridge; no
> hardware, no vendor command, no domain 0/41.

## Setup

```bash
# plant (domain 43)
cd jcheng-simulation
source /opt/ros/jazzy/setup.bash && source install/setup.bash
ROS_DOMAIN_ID=43 ros2 launch jcheng_simulation_robots robot.launch.py \
    robot:=armpi_ultra backend:=mock

# runtime (domain 42, bridge in a second context on 43)
cd jcheng-system
source /opt/ros/jazzy/setup.bash
source ../jcheng-interfaces/install/setup.bash && source install/setup.bash
ROS_DOMAIN_ID=42 JCHENG_ROOT=$PWD ros2 run jcheng_runtime jcheng_runtime \
    --ros-args -p robot:=armpi_ultra -p sim_bridge:=true

# probe (domain 42, joint state read in a second context on 43)
ROS_DOMAIN_ID=42 python3 tools/teleop_sim_probe.py \
    --joints 5 --velocity 0.5,0,0,0,0 --sim-domain 43
```

`/sim/joint_states` had exactly one publisher before the probe started
(checked with `ros2 topic info`). The probe fails on a rejected arm or lease.

## Result

```text
arm True armed
lease True lease-4500d4b38246 OK
initial_positions [0.0, 0.0, 0.0, 0.0, 0.0, -0.4]
sim_positions [0.23752462994307283, 0.0, 0.0, 0.0, 0.0, -0.4]
```

`joint1` moved 0 → 0.2375 rad through `TeleopIntake` → `MotionArbiter`
(`JOINT_STEP`, profile `pose`) → `RosSimHardwareBridge` on domain 43. The sixth
value is the plant's gripper joint, which the 5-joint ArmPi profile does not
command and which stayed at its initial -0.4 rad. The probe released the lease
on exit.

## Findings

- The `--sim-domain` option reads `/sim/joint_states` in a second `rclpy`
  context (`domain_id=43`) while all services, lease and `TeleopCommand`
  traffic stay on domain 42. One tool now covers both the domain-43 harness and
  the canonical split.
- The first attempt returned stale results because two orphaned mock plants
  from an earlier launch were still publishing `/sim/joint_states`; the launch
  parent exiting does not kill the mock child. Always check the publisher count
  before recording a run.
- The installed `jcheng_system_core` was stale after the PiPER X profile change;
  `colcon build --base-paths ros2_ws/src` is required before a runtime run.

## Limits

- Test profile only; no hardware claim.
- The arbiter's `bus_read` sees the simulation state at the step boundary; the
  plant's first-order tracking lag is not characterized as a bound.
- The bridge is test-only and not a deployment component.
- No vendor executor, bus read, e-stop or physical stop confirmation.
