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
colcon build --base-paths ros2_ws/src   16 packages finished
python3 -m pytest -q                    pass
python3 -m pytest -q tests_ros          pass (16 tests)
bash scripts/check_m5_vendor.sh         M5.2 gate OK
python3 -m pytest tests_ros/test_gateway_vendor_node.py -v   1 passed
```

The entry point `jcheng_gateway_vendor` was installed, and the R10 module
imported under Humble. Raw output: [`humble-build.txt`](humble-build.txt),
[`humble-import.txt`](humble-import.txt).

### 2.2 Startup fix and fake-vendor ROS graph

The first version read the bus synchronously in `__init__`, before the node was
added to an executor. Under a real ROS launch the service future would never be
processed, so startup could deadlock. R10 now does the first `GetBusServoState`
from a one-shot timer after the executor is spinning; HTTP and the tick thread
start only after a successful measured read.

`tests_ros/test_gateway_vendor_integration.py` runs the **real** R10 node
against a fake vendor controller in the Humble container:

- fake controller serves `/ros_robot_controller/bus_servo/get_state` and
  consumes `/servo_controller`;
- the R10 node starts HTTP only after the initial bus read;
- `/v1/health` reports `capabilities: ["hardware"]`;
- arm, lease and `/v1/motion/execute` complete with `bus_verified=true`;
- `/v1/motion/stop` is accepted and latches safety.

Result: **1 passed** in 2.16 s. Raw output:
[`humble-integration.txt`](humble-integration.txt),
[`humble-clean.txt`](humble-clean.txt). No hardware or physical motion is
involved.

## 3. Not verified

- Launch against the real vendor stack and container (DDS discovery, real
  service timing, publisher subscriber count, stop hold latency). The
  integration test uses a fake vendor node in the same Humble container.
- Hardware C1–C5 and any physical motion or physical-stop claim.
- Application-side M4 real session: the target gate is implemented offline
  (`--target hardware` + L5 + `hardware` capability), but no hardware run.

No hardware was opened and no command was published. This evidence is not a
hardware validation and does not close M4/M5.
