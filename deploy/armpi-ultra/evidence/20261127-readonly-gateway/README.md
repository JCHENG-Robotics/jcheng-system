# Read-only `/v1` gateway deployment — 2026-11-27

> Evidence class: **L4 read-only, robot** (HTTP GET + WebSocket read on the
> robot's loopback). No motion command, no `POST` accepted, no container
> restart, no device opened. This is **not** hardware validation of the system
> API's motion paths.

## 1. Artifact

| Item | Value |
|---|---|
| Host unit | `jcheng-system-readonly.service` (enabled, `Restart=always`) |
| Container node | `ros2 run jcheng_telemetry jcheng_telemetry_node` |
| Workspace | `/home/ubuntu/jcheng-head/ros2_ws` (built in `ArmPiUltra`) |
| Source | `jcheng-system` at `origin/main` `9ae0fe6` + this deploy branch |
| Interfaces | v0.1.0 / `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42` |
| Profile | `sha256:2098e5e5ece44bca0fd347a6c8a31e536b3cf9d5ff2352ac648290589d1c32ba` |
| Domain | 0 (vendor), `rmw_cyclonedds_cpp` |
| Bind | `127.0.0.1:8767` |
| Token | `/etc/jcheng-system/readonly.env` (0600, generated; not in git) |

Build (inside the container):

```
colcon build --packages-select jcheng_interfaces jcheng_system_core jcheng_hardware jcheng_telemetry
Summary: 4 packages finished [2min 52s], BUILD_RC=0
```

## 2. Verification

`GET /v1/health` → 200:

```json
{"status": "degraded", "mode": "readonly", "writes": 0, "domain_id": 0,
 "contract": {"major": 0, "minor": 1, "patch": 0, "contract_hash": "sha256:0b8d72…"},
 "profile_hash": "sha256:2098e5e5…"}
```

| Route | Result |
|---|---|
| `/v1/health` | 200 (unauthenticated by design) |
| `/v1/contract` | 200 |
| `/v1/state` | 200 — 5 joints, `fresh: true`, `source: jcheng_hardware` |
| `/v1/safety` | 200 — `authoritative: false` |
| `/v1/battery` | 200 — `voltage_v: 0.0`, `fresh: false` |
| `/v1/topics`, `/v1/tf`, `/v1/cameras` | 200 |
| `/v1/events` | 200 |
| `/v1/ws/state` (Upgrade) | **101 Switching Protocols** |
| `/v1/capabilities` | 404 — the read-only gateway has no capabilities route |
| `POST /v1/motion/stop`, `/v1/safety/arm`, `/v1/leases`, `/v1/motion/execute` | **403** `read-only gateway: all mutation methods are rejected` |
| `systemctl restart` → next `/v1/health` 200 | 3–4 s |

## 3. Limits and open items

- `status: degraded` because the read-only bus service
  (`/ros_robot_controller/bus_servo/get_state`) is not ready and the battery
  topic has no publisher at the time of the run. Joint state, TF and the
  read-only telemetry streams are live.
- The gateway is loopback-only. The operator console needs an SSH tunnel; a
  managed tunnel unit does not exist yet.
- `/v1` still has **no teleop route**. Teleop remains a domain-42 DDS intake in
  `jcheng_runtime` (mock only) and is not part of this deployment.
- `jcheng-system-readonly` was installed on the robot from this branch; nothing
  in the vendor container, the vendor units or the pose gateway (8766) was
  touched or restarted.
