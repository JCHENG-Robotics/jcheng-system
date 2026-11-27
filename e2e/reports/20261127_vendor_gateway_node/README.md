# Hardware gateway ROS node — evidence

> Date: 2026-11-27. Level: **L1 + Humble build** (syntax check, unit suite,
> gate, `ros:humble` build with real vendor message definitions). No vendor
> stack, no device and no hardware. Target: `jcheng_system_core` gateway + M5.2
> `jcheng_vendor` executor.

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

### 2.1 Humble build (2026-11-27)

A disposable `armpi-humble-moveit:latest` container built the repository with
`jcheng_interfaces` and the **real** vendor message definitions from the
frozen baseline (`servo_controller_msgs`, `ros_robot_controller_msgs`, mounted
read-only; no vendor hardware):

```text
colcon build --base-paths ros2_ws/src   16 packages finished [24.6s]
python3 -m pytest -q                    pass
python3 -m pytest -q tests_ros          pass (15 tests)
bash scripts/check_m5_vendor.sh         M5.2 gate OK
python3 -m pytest tests_ros/test_gateway_vendor_node.py -v   1 passed
```

The entry point `jcheng_gateway_vendor` was installed, and the R10 module
imported under Humble. Raw output: [`humble-build.txt`](humble-build.txt),
[`humble-import.txt`](humble-import.txt).

The offline composition tests (`tests/test_vendor_gateway_stack.py`) prove
`/v1/motion/execute` reaches `HardwareExecutor` over a fake bridge; they do not
import the ROS node.

## 3. Not verified

- Launch against the running vendor stack (rclpy timers, DDS discovery, bus
  service, publisher subscriber count). The Humble build used mounted message
  definitions, not a live vendor graph.
- Vendor stack integration: service availability, bus polling, publisher
  subscriber count, stop hold latency.
- Hardware C1–C5 and any physical motion or physical-stop claim.
- Application-side M4 enablement (non-mock gateway, L5 evidence, explicit
  per-session authorization).

No hardware was opened and no command was published. This evidence is not a
hardware validation and does not close M4/M5.
