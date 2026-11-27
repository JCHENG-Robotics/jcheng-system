# Robot precheck — 2026-11-27

> Level: **L4 read-only** (legacy 8765 gateway HTTP GET only). No motion
> command, no arm, no execute, no stop, no container restart, no device opened.
> The operator reported the robot powered on; this is what was reachable.

## 1. Reachability

| Probe | Result |
|---|---|
| `10.42.0.218:22` | open, but `pi` is a **time-sync ForceCommand** (`get` / `set <epoch>`); no shell and no SFTP |
| `10.42.0.218:8765` | open: legacy read-only gateway, token required |
| `10.42.0.218:8766` | closed: no supervised gateway |
| Aux UVC camera | available, 640×480, 15 fps |
| TF | available: 11 dynamic edges, freshest ≈1 ms |
| Depth camera | unavailable |
| Battery | unavailable; no `/ros_robot_controller/battery` message |
| Joint state | empty; `joints_ready=false` |
| Bus measured state | empty; `measured_ready=false` |
| Motion | `motion_enabled=false`, `motion_state=disabled` |

Raw responses: [`health.json`](health.json), [`state.json`](state.json),
[`camera.json`](camera.json), [`battery.json`](battery.json). The gateway token
is not stored here.

## 2. Interpretation

- The robot host is reachable and the legacy read-only gateway is alive.
- The vendor telemetry needed for motion is **not** publishing: no battery, no
  joint state, no bus measurement, no depth camera. The aux camera and TF come
  from other nodes and are alive.
- `8766` is down. Even if it were started, it is the legacy supervised gateway,
  not the chosen R10 system gateway (`jcheng_vendor/gateway_node.py`).
- The `pi` SSH account cannot be used to deploy R10: it only supports the
  time-sync `get`/`set` command and has no SFTP subsystem.

## 3. Why no R10 launch or C1–C5

R10 needs a shell to install/build the system workspace and start the vendor
bridge. The only SSH account reachable is time-sync-only, and the supervised
gateway is down. C1–C5 would also require explicit per-session motion
authorization and a healthy vendor stack; neither is present.

Nothing was restarted. The vendor container was not touched.

## 4. Needed to continue

1. Normal shell access to the robot host (a user/port with a shell; or restore
   the `pi` shell) so R10 can be deployed from
   `feat/vendor-gateway-node-20261127`.
2. The vendor controller and battery/joint topics publishing again (the
   battery and joint state are empty; do not restart the vendor container from
   the laptop).
3. A separately authorized motion session for C1–C5. This precheck grants no
   motion authority.
