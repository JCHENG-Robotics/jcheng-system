# 运行手册（M3 Mock / M4 只读 / M5.1 Mock ROS）

> M3 只运行 Mock；M4 只读接入真实厂商状态；M5.1 只通过 ROS 服务/action
> 操作 Mock 硬件。**不打开串口、不发布厂商命令、不发送任何运动。**
> M5.2 实机运动需要单独授权。

## 1. 构建与测试

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash
python3 -m pytest -q
```

## 2. 运行场景

```bash
ros2 run jcheng_testkit jcheng_scenarios --root . \
  --out e2e/reports/m3-mock.json
```

预期：15 passed、2 skipped、required failures 0。

## 3. 运行 Mock 网关

```bash
export JCHENG_GATEWAY_TOKEN="<secret>"
ros2 run jcheng_gateway jcheng_gateway --port 8766
# 或
ros2 launch jcheng_bringup mock_stack.launch.py token:="$JCHENG_GATEWAY_TOKEN"
```

- 默认只绑定 `127.0.0.1`。
- 非 health 端点需要 `Authorization: Bearer <token>`。
- 不连接 ROS 硬件；核心是进程内 Mock。

## 4. 诊断 CLI

```bash
ros2 run jcheng_safety jcheng_safety_check
ros2 run jcheng_authority jcheng_authority_check
ros2 run jcheng_motion jcheng_motion_check
ros2 run jcheng_hardware jcheng_hardware_check
ros2 run jcheng_guard jcheng_guard_check
```

## 5. 锁文件验证

```bash
python3 tools/verify_lock.py --root . \
  --interfaces-root ../jcheng-interfaces \
  --require-evidence
```

`--remote` 会检查 interfaces v0.1.0 标签提交。

## 6. M4 只读运行（需单独授权）

### 6.1 离线

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash
bash scripts/check_m4_readonly.sh
python3 -m pytest -q
ros2 run jcheng_telemetry jcheng_readonly_probe \
  --root . --duration-s 3 --interval-s 0.2 \
  --out e2e/reports/m4-readonly.json \
  --evidence e2e/reports/m4-readonly.jsonl
```

预期：`writes=0`、`measured_samples>0`、preflight `ok=true`。

### 6.2 实机只读（不运动）

前置：厂商栈已运行；**不重启厂商容器**；有线链路、DDS 发现、时钟偏移在阈值内；
操作员与监督人确认；已获得本次只读运行的单独授权。

```bash
export JCHENG_ROOT="$PWD"
export JCHENG_GATEWAY_TOKEN="<secret>"
export JCHENG_READONLY_EVIDENCE="/tmp/jcheng-m4-readonly.jsonl"
export ROS_DOMAIN_ID=0
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
# On the robot use the vendor DDS file so the vendor state topics are visible.
# The repository template is for workstation-side domain setup.
export CYCLONEDDS_URI="file:///home/ubuntu/cyclonedds.xml"
ros2 launch jcheng_bringup readonly.launch.py \
  root:="$JCHENG_ROOT" \
  gateway_token:="$JCHENG_GATEWAY_TOKEN" \
  gateway_port:=8767
```

检查：

```bash
curl -s http://127.0.0.1:8767/v1/health
curl -s -H "Authorization: Bearer $JCHENG_GATEWAY_TOKEN" \
  http://127.0.0.1:8767/v1/state
curl -s -H "Authorization: Bearer $JCHENG_GATEWAY_TOKEN" \
  http://127.0.0.1:8767/v1/safety
```

- `truth` 必须是 `measured`、`verification` 必须是 `bus`；否则视为未通过。
- 任何 `POST`/`DELETE` 必须返回 403。
- 运行 **≥30 分钟**，停止后检查 evidence JSONL 与 `.summary.json`：
  `writes=0`、`measured_samples>0`、无命令发布/写服务。
- **旧网关的 `measured` 是命令缓存，不是总线真值。** 用独立
  `GetBusServoState` 同刻读数对比；2026-11-23 实机上新节点与独立读数
  逐项一致，旧网关缓存最大差 387 pulses。
- 能力域进程看不到厂商命令话题（域隔离验证）。
- 相机 topic 未发布时 `/v1/cameras` 为空；`/tf` 正常；`/tf_static` 需要
  TRANSIENT_LOCAL 订阅。
- **全程没有发送任何运动命令。**

2026-11-23 实机结果：30 分钟、1799/1799 `measured`、`writes=0`、`ok=true`；
证据见 `jcheng-manipulation/docs/ecosystem/evidence/m4-readonly-20261123/`。

## 7. M5.1 Mock ROS 传输

```bash
source /opt/ros/humble/setup.bash
source install/setup.bash
export JCHENG_ROOT="$PWD"
python3 -m pytest -q tests_ros
ros2 launch jcheng_bringup motion_mock.launch.py root:="$JCHENG_ROOT"
```

另一个终端：

```bash
ros2 service list | grep /jcheng/
ros2 service call /jcheng/safety/get_state \
  jcheng_interfaces/srv/GetSafetyState "{}"
```

- 默认 `DISARMED`。
- `ValidateMotion` 只校验；`ExecuteMotion` 只执行 Mock 硬件。
- `/safety_stop=true` 会锁存并拒绝新命令。
- 不连接机器人、不发布 `/servo_controller`、不打开设备。

## 8. M5.2 实机运动（待单独授权）

M5.2 才接入 `/servo_controller` 硬件桥、单关节 0.05 rad、夹爪开/合和
软件停止实测。开始前必须：清空工作区、支撑机械臂、确认电池/链路/总线、
操作员与监督人就位，并获得单独明确授权；旧网关保持只读。
