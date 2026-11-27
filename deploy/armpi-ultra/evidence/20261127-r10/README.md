# R10 gateway live evidence — 2026-11-27

Scope: the hardware-backed `jcheng_gateway_vendor` node built and served on the
robot for the Studio `/v1` client. **No motion command was sent.** The motion
path (`/v1/motion/execute`) was not exercised.

## Environment

- Robot host, `ArmPiUltra` container, ROS 2 Humble, vendor domain 0,
  CycloneDDS (`/home/ubuntu/cyclonedds.xml`).
- Vendor stack running (`start_node.service`); `ros_robot_controller` opened
  `/dev/rrc`; `GetBusServoState` available.
- Workspace `/home/ubuntu/jcheng-r10` built from this checkout plus
  `jcheng_interfaces` v0.1.0; gateway bound `127.0.0.1:8766`.
- Unit `jcheng-gateway-vendor.service` started manually, `enable_motion=true`,
  never enabled at boot. Legacy pose gateway masked.

## Results

| Check | Result |
|---|---|
| `GET /v1/health` | `status=ok` |
| contract hash | `sha256:0b8d72…24b42` (matches Studio pin) |
| profile hash | `sha256:2098e5…c32ba` |
| `GET /v1/state` | `fresh=true`, `truth=measured`, `verification=bus`, 5 joints |
| `GET /v1/safety` | `state=DISARMED`, `stop_latched=false`, no violations |
| Studio `gateway_v1` / `session` | available |
| Studio `system_stop` | available (proxy not exercised) |

## Files

- `health.json` — unauthenticated health payload.
- `contract.json` — authenticated contract payload.
- `safety.json` — authenticated safety payload.
- `state-summary.json` — state fields used for acceptance (no positions).
- `ports.txt` — loopback ports and the masked legacy gateway.

## Limits

- `state.battery_voltage` is `0.0` in this build: the vendor bridge reads the
  bus servos but does not subscribe to the battery topic. The legacy 8765
  gateway still reports the live pack voltage; the Studio `gateway_v1`
  readiness does not depend on this field.
- `/v1/motion/execute` and `/v1/motion/stop` were not called. A lease and an
  explicit operator authorization are still required.
- The bus-servo read has no safe timeout; a wedged reader is an operator
  restart, not an automatic retry.
