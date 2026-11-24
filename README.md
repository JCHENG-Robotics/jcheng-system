# jcheng-system

JCHENG Robotics 的特权运行时与系统集成点。

> 状态：**v0.3.0-dev — M5.2 核心实机通过；M6 S13 回放 21/21、S17 plan-only 离线通过。**
> M4 实机只读已通过：30 分钟、1799/1799 `measured`、`writes=0`。
> M5.2 单关节、夹爪开/合、停止、停止运行时后拒绝、无租约/越限均验证；
> 夹爪采用定时完成 + 总线证据；手臂仍 ±8 pulse。S7/S9 未单独实机验证。
> M6 离线开始：v1.0.0 G8 轨迹回放 decision 21/21 一致；未执行运动。

## 1. 当前交付

| 包 | 作用 |
|---|---|
| `jcheng_system_core` | 纯 Python Mock 核心：配置、安全状态机、租约、仲裁、Mock 硬件、guard、网关、证据、场景 |
| `jcheng_safety` | 安全诊断 CLI |
| `jcheng_authority` | 租约诊断 CLI |
| `jcheng_motion` | 仲裁器诊断 CLI |
| `jcheng_hardware` | Mock 硬件状态 CLI + 只读总线桥（`readonly.py`） |
| `jcheng_gateway` | Mock HTTP 网关入口 |
| `jcheng_guard` | 发布者白名单诊断 |
| `jcheng_testkit` | S1–S17 场景运行器 |
| `jcheng_telemetry` | 只读遥测缓存、只读网关、预检、证据、短时探针 |
| `jcheng_runtime` | M5.1 ROS 传输：租约/安全/校验服务、`ExecuteMotion` action、停止锁存（Mock） |
| `jcheng_vendor` | M5.2 受监督硬件运行时：单关节/夹爪、`/servo_controller`、总线复核 |
| `jcheng_moveit` | M6 S17 plan-only goal 构建/校验；不执行 MoveIt |
| `jcheng_bringup` | `mock_stack.launch.py` + `readonly.launch.py` + `motion_mock.launch.py` + `motion_vendor.launch.py` |

## 2. 架构（M3）

```text
HTTP client
  → jcheng_gateway (token/rate limit)
  → jcheng_system_core.MotionArbiter
      → LeaseManager
      → SafetySupervisor
      → PublisherGuard
      → MockHardwareBridge
      → EvidenceStore
```

- 只有 `MockHardwareBridge` 记录/发布 Mock 命令；guard 允许的发布者为
  `jcheng_hardware`。
- 完成判定来自 Mock 总线读数；总线失败锁存 `UNKNOWN`。
- 契约哈希固定为 interfaces v0.1.0：
  `sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42`。
- M4 增加只读接入；M5.1 增加 ROS 服务/action 传输（仍为 Mock）。

### 2.1 M4 只读链路

```text
厂商节点（域 0）
  ├─ /controller_manager/joint_states  → truth=commanded
  ├─ /ros_robot_controller/battery
  └─ /ros_robot_controller/bus_servo/get_state（GetBusServoState，只读位置）
        → jcheng_telemetry（域 0）
            → /jcheng/telemetry/state（RobotState）
            → /jcheng/telemetry/safety（SafetyState，只读视图）
            → 127.0.0.1 只读 HTTP 网关（GET only）
```

- 只发布 `RobotState`/`SafetyState`；没有命令发布者。
- 只调用 `GetBusServoState`，只设置 `get_position=1`，舵机 ID 1..6 严格递增。
- 不打开串口/设备；不重启厂商容器；不使能、不租约、不运动。
- M4 遥测运行在域 0；41/42 配置已准备，域桥接是 M5 的前置条件。

### 2.2 M5.1 ROS 传输（Mock）

```text
HTTP/CLI client
  → /jcheng/authority/*_lease        （LeaseManager）
  → /jcheng/safety/validate_motion   （SafetySupervisor，纯校验）
  → /jcheng/safety/{arm,disarm,reset,get_state}
  → /jcheng/motion/execute           （ExecuteMotion action）
      → MotionArbiter → MockHardwareBridge
  → /safety_stop (Bool, true)        → STOP_LATCHED
```

- 默认 `DISARMED`；使能需要检查清单与确认短语。
- 运动必须携带有效租约；无租约/过期/抢占/撤销都拒绝。
- `ValidateMotion` 只校验，不执行、不发布命令。
- `/safety_stop=true` 立即锁存并拒绝新命令。
- M5.1 只使用 `MockStack`；没有厂商命令发布者、没有写服务、没有设备访问。
- M5.2 的 `/servo_controller` 硬件桥与单关节/夹爪已实现并实机通过核心验收；
  夹爪使用定时完成 + 总线证据，手臂仍 ±8。S7/S9 运行时检测残余。

## 3. 快速开始

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash

# 28 项契约/场景/只读测试（27 pass / 1 conditional skip）
python3 -m pytest -q

# M5.1 Mock ROS 传输：5 项服务/action/停止测试
python3 -m pytest -q tests_ros

# M5.1 Mock 运行时（租约/安全/校验/action；不连接硬件）
ros2 launch jcheng_bringup motion_mock.launch.py root:="$PWD"

# S1–S17（15 pass / 2 skipped）
ros2 run jcheng_testkit jcheng_scenarios --root . \
  --out e2e/reports/m3-mock.json

# M4 短时只读探针（Mock；不连接机器人）
ros2 run jcheng_telemetry jcheng_readonly_probe \
  --root . --duration-s 3 --interval-s 0.2 \
  --out e2e/reports/m4-readonly.json \
  --evidence e2e/reports/m4-readonly.jsonl

# M4 只读节点（实机；需要单独授权；不发送运动）
# export JCHENG_ROOT="$PWD"
# export JCHENG_GATEWAY_TOKEN="<secret>"
# ros2 launch jcheng_bringup readonly.launch.py \
#   root:="$JCHENG_ROOT" gateway_token:="$JCHENG_GATEWAY_TOKEN"

# Mock HTTP 网关（需要 token；不连接硬件）
ros2 run jcheng_gateway jcheng_gateway --port 8766 --token "<secret>"
# 或
ros2 launch jcheng_bringup mock_stack.launch.py token:="<secret>"
```

Mock 网关端点（核心子集）：`/v1/health`、`/v1/contract`、`/v1/state`、
`/v1/safety`、`/v1/leases`、`/v1/motion/validate`、`/v1/motion/execute`、
`/v1/motion/stop`、`/v1/capabilities`、`/v1/events`。

只读网关端点：`/v1/health`、`/v1/contract`、`/v1/state`、`/v1/safety`、
`/v1/battery`、`/v1/topics`、`/v1/cameras`、`/v1/tf`、`/v1/events`。
所有非 GET 方法返回 403；没有 motion/lease/arm/reset 路由。

## 4. 安全边界

- 默认 `DISARMED`；使能需要操作员、检查清单与确认短语。
- 状态过期、停止锁存、profile/契约哈希不匹配、无租约、未授权发布者、
  总线失败都会拒绝或锁存。
- 停止是“软件停止”，不是急停。
- 平台没有急停/抱闸/独立编码器/自碰撞模型。
- M4 只读链路没有命令发布者、没有写服务、没有串口/设备访问。
- `/controller_manager/joint_states` 只标记 `commanded`，不能作为完成依据。
- M5.1 的租约/安全/`ExecuteMotion` 只作用于 Mock 硬件；`/safety_stop=true`
  立即锁存并拒绝新命令。
- 任何实机运动仍需单独、明确的现场授权；M5.1 没有实机动作。

## 5. 验收

- M3：15 个必选场景通过，2 个按计划跳过；16 项 pytest；`m3-mock` 通过。
- M4（离线）：28 项 pytest（27 pass / 1 conditional skip）；短时只读探针
  `writes=0`；`m4-readonly` 构建 10 个系统包并通过只读门禁。
- M5.1（Mock）：5 项 `tests_ros` 通过（租约、安全、校验、停止锁存、action
  完成）；`m5-mock` 构建 11 个系统包并通过 Mock-only 门禁。
- M5.2（实机，核心通过）：joint1 +0.05 rad（505 vs 512）；夹爪闭 183→645、
  开 647→183，均 `COMPLETED`/`bus_verified`；停止 hold 0.266 ms；
  停止运行时后 action server 不存在；无租约/越限拒绝。
- 残余：S7 状态过期注入、S9 运行时发布者检测未单独实机验证。
- M6（离线）：S13 回放 21/21 decision 一致（G8 acceptance 11/11、
  fast run2 10/10）；S17 plan-only goal 构建/校验与测试通过；未执行运动。
- M4（实机）：**通过**；30 分钟、1799/1799 `measured`、`writes=0`；
  独立总线读数与新节点逐项一致；域 42 看不到厂商命令话题。
- 关键发现：旧网关 `measured` 是命令缓存（`truth=commanded`），与真实总线
  最大相差 387 pulses；M4 以独立 `GetBusServoState` 读数为准。
- 报告：`e2e/reports/m3-mock.json`、`e2e/reports/m4-readonly.json`；
  实机证据：`jcheng-manipulation/docs/ecosystem/evidence/m4-readonly-20261123/`。

详见 [M3 范围](docs/M3-SCOPE.md)、[M4 范围](docs/M4-SCOPE.md)、
[M5 范围](docs/M5-SCOPE.md)、[M6 范围](docs/M6-SCOPE.md) 与
[验收](docs/ACCEPTANCE.md)。

## 6. M6 与后续

1. M5.2 核心实机验收通过；S7/S9 运行时检测残余。
2. M6 S13 回放 21/21；S17 plan-only goal 构建/校验通过。
3. M6 待完成：真实 MoveIt plan-only、S14 实机底盘、G8 全流程、落点证据。
4. G8/底盘实机需单独明确授权；旧网关仍保留只读回滚。

## 7. 许可

许可证审查进行中；本仓库没有 `LICENSE`，默认版权保留。
详见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
