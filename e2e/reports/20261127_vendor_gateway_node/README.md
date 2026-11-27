# Hardware gateway ROS node — evidence

> Date: 2026-11-27. Level: **L0/L1 offline** (syntax check, unit suite, gate).
> No ROS build, no vendor stack, no device and no hardware in this session.
> Target: `jcheng_system_core` gateway + M5.2 `jcheng_vendor` executor.

## 1. What the node does

`jcheng_vendor/gateway_node.py`:

- refuses to start unless `enable_motion:=true` and a token
  (`JCHENG_GATEWAY_TOKEN` or the `token` parameter) are present;
- owns the only `VendorBridge` (`/servo_controller` publisher,
  `GetBusServoState` client);
- performs one initial bus read before starting the HTTP server or the tick
  thread; a failed read refuses startup rather than serving stale state;
- polls the bus at about `poll_s=0.25` and calls `safety.update_state`;
- serves `GatewayApp` via `GatewayServer` (mode `hardware`, capabilities
  `["hardware"]`);
- runs `TickThread` (20 Hz `arbiter.tick`);
- routes `/safety_stop=true` to `HardwareExecutor.stop`.

Launch: `jcheng_bringup/launch/gateway_vendor.launch.py`. The token is read
from the environment, host defaults to loopback, and motion is off by default.

## 2. Verification

```text
python3 -m py_compile gateway_node.py gateway_vendor.launch.py   pass
python3 -m pytest -q                                            pass
scripts/check_repo_gate.sh                                      OK
```

Raw output: [`compile.txt`](compile.txt), [`pytest.txt`](pytest.txt),
[`gate.txt`](gate.txt).

The offline composition tests (`tests/test_vendor_gateway_stack.py`) prove
`/v1/motion/execute` reaches `HardwareExecutor` over a fake bridge; they do not
import the ROS node.

## 3. Not verified

- Humble build and launch (rclpy, vendor messages, DDS QoS).
- Vendor stack integration: service availability, bus polling, publisher
  subscriber count, stop hold latency.
- Hardware C1–C5 and any physical motion or physical-stop claim.
- Application-side M4 enablement (non-mock gateway, L5 evidence, explicit
  per-session authorization).

No hardware was opened and no command was published. This evidence is not a
hardware validation and does not close M4/M5.
