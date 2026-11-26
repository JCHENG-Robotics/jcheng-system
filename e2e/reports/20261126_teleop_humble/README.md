# Teleop intake Humble validation

> Status: **Partially verified (L1/L2, ROS 2 Humble)**, 2026-11-26.
> Container: `armpi-humble-moveit:latest`; source mounts read-only; no hardware.

## Command

```bash
docker run --rm \
  -v /home/jerichocheng/jcheng/jcheng-interfaces:/interfaces:ro \
  -v /home/jerichocheng/jcheng/jcheng-system:/system:ro \
  -w /tmp/ws armpi-humble-moveit bash -lc '
    source /opt/ros/humble/setup.bash
    mkdir -p src
    ln -sf /interfaces/ros2_ws/src/jcheng_interfaces src/jcheng_interfaces
    ln -sf /system/ros2_ws/src/jcheng_system_core src/jcheng_system_core
    ln -sf /system/ros2_ws/src/jcheng_runtime src/jcheng_runtime
    colcon build --packages-select jcheng_interfaces jcheng_system_core jcheng_runtime
    source install/setup.bash
    export PYTHONPATH=/tmp/ws/src/jcheng_system_core:/system/ros2_ws/src/jcheng_hardware:/system/ros2_ws/src/jcheng_telemetry:/system/ros2_ws/src/jcheng_moveit:$PYTHONPATH
    cd /system
    python3 -m pytest -q tests
    python3 -m pytest -q tests_ros
  '
```

## Result

```text
jcheng_interfaces, jcheng_system_core, jcheng_runtime: build pass
tests: 45 passed
tests_ros: 12 passed
```

The container's pytest reports `Unknown config option: pythonpath`; the source
paths were supplied via `PYTHONPATH`, matching the package paths used by the
repository. This is a container-tooling warning, not a test failure.

## Scope

- Mock system core, ROS subscriber, and the `TeleopIntake` path only.
- No simulation plant in the Humble run; the ArmPi domain-43 L3 probe is a
  separate Jazzy result.
- No hardware, vendor executor, domain 0/41/42 deployment or stop
  confirmation.
