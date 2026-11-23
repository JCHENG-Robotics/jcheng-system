# 运行手册（M3 Mock / M4 只读）

> M3 只运行 Mock；M4 只读接入真实厂商状态，不打开串口、不发布厂商命令、
> **不发送任何运动**。任何实机运行需要单独授权。

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
export CYCLONEDDS_URI="file://$JCHENG_ROOT/configs/dds/cyclonedds.domain0.xml"
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
- 同一时刻与旧网关脉冲读数比较，容差按脉冲。
- 能力域进程看不到厂商命令话题（域隔离验证）。
- **全程没有发送任何运动命令。**

## 7. 实机运动

M4 不包含运动步骤。M5 起单关节/夹爪/停止需要单独评审与单独授权；
旧网关在迁移期保持只读。
