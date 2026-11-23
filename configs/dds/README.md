# DDS domain templates (M4)

M4 uses CycloneDDS unicast peers. The templates are intentionally small and
must be adapted to the wired addresses of the robot and the workstation:

| File | Domain | Members |
|---|---|---|
| `cyclonedds.domain0.xml` | 0 | vendor nodes, `jcheng_hardware`, read-only telemetry |
| `cyclonedds.domain41.xml` | 41 | privileged motion plane (no motion node in M4) |
| `cyclonedds.domain42.xml` | 42 | capability plane (no capability node in M4) |
| `cyclonedds.domain43.xml` | 43 | simulation and CI only |

Usage:

```bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export CYCLONEDDS_URI="file://$PWD/configs/dds/cyclonedds.domain0.xml"
```

Before use, replace the `10.42.0.218` peer with the actual **peer** address:
on the robot that is the workstation, and on the workstation that is the robot.
`127.0.0.1` is kept for same-host tests.

M4 does not run a motion or capability process. The domain files are staged so
M5 can enforce isolation; the M4 read-only probe runs in domain 0 because that
is where the vendor state topics live. Serving the read-only API from domain 0
is a documented M4 limitation, not the final topology.
