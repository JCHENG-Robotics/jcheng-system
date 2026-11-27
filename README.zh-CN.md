# jcheng-system

**简体中文** | [English](README.md)

JCHENG Robotics 的特权运行时与系统集成点。

> 状态：**实验性** — v0.3.0-dev，截至 2026-11-26。M4 只读实机、M5.2 核心运动、
> M6 离线回放/真实 MoveIt plan-only、teleop intake 与仅测试用域 43 仿真桥已在
> mock/仿真上验证。S7（状态过期注入）与 S9（运行时发布者检测）尚未在实机验证，
> 未作出 teleop 硬件或 PiPER X L3 声明。本运行时不是安全认证系统。
> 本文为英文主文档的中文对照；如有歧义，以英文版为准。

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
| `jcheng_bringup` | `mock_stack` + `readonly` + `motion_mock` + `motion_vendor` launch |

## 2. 架构

```text
HTTP client
  → jcheng_gateway (token/rate limit)
  → jcheng_system_core.MotionArbiter
      → LeaseManager
      → SafetySupervisor
      → PublisherGuard
      → HardwareBridge (mock 或 vendor)
      → EvidenceStore
```

- 只有硬件桥发布命令；guard 允许的发布者为 `jcheng_hardware`。
- 完成判定来自总线读数，不来自命令缓存；总线失败锁存 `UNKNOWN`。
- 契约哈希固定为 interfaces v0.2.0：
  `sha256:afca22a42f7163e0351d185d5a8577b0b51f2f3da4b92278f161a4a7d2c35b1c`。

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
- M4 遥测运行在域 0；41/42 配置已准备，域桥接尚未实现。

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
- M5.1 只使用 `MockStack`：没有厂商命令发布者、没有写服务、没有设备访问。
- M5.2 增加 `/servo_controller` 硬件桥；单关节与夹爪已通过核心实机验收。
  夹爪使用定时完成 + 总线证据；手臂保持 ±8 pulse 容差。

## 3. 依赖

- 机器人：ROS 2 Humble；本机可用 Jazzy 构建。
- Python ≥ 3.10、`pytest`、`PyYAML`。
- Mock 与只读离线路径不需要硬件；vendor 路径需要机器人与单独授权。

## 4. 快速开始

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

# 短时只读探针（Mock；不连接机器人）
ros2 run jcheng_telemetry jcheng_readonly_probe \
  --root . --duration-s 3 --interval-s 0.2 \
  --out e2e/reports/m4-readonly.json \
  --evidence e2e/reports/m4-readonly.jsonl
```

Mock 网关需要 token：

```bash
ros2 run jcheng_gateway jcheng_gateway --port 8766 --token "<secret>"
# 或
ros2 launch jcheng_bringup mock_stack.launch.py token:="<secret>"
```

Mock 网关端点：`/v1/health`、`/v1/contract`、`/v1/state`、`/v1/safety`、
`/v1/leases`、`/v1/motion/validate`、`/v1/motion/execute`、
`/v1/motion/stop`、`/v1/capabilities`、`/v1/events`。
只读网关端点：`/v1/health`、`/v1/contract`、`/v1/state`、`/v1/safety`、
`/v1/battery`、`/v1/topics`、`/v1/cameras`、`/v1/tf`、`/v1/events`。
所有非 GET 方法返回 403；没有 motion/lease/arm/reset 路由。

两个网关还提供只读 WebSocket 流 `/v1/ws/state`（需要 Bearer token）。升级后
服务端先发一个 `hello` 信封，然后以约 10 Hz 发送 `state` 与 `safety` 信封，
格式遵循 `jcheng-interfaces/schemas/ws/jcheng-ws.v1.json`。客户端 ping 会收到
pong；客户端 close 结束流；其他客户端数据被忽略。该流没有租约、运动、事件或
写入路径。

## 5. 安全边界

- 默认 `DISARMED`；使能需要操作员、检查清单与确认短语。
- 状态过期、停止锁存、profile/契约哈希不匹配、无租约、未授权发布者、
  总线失败都会拒绝或锁存。
- 停止是“软件停止”，不是急停。
- 平台没有急停/抱闸/独立编码器/自碰撞模型。
- M4 只读链路没有命令发布者、没有写服务、没有串口/设备访问。
- `/controller_manager/joint_states` 只标记 `commanded`，不能作为完成依据。
- 任何实机运动仍需单独、明确的现场授权。

## 6. 验证状态

| 结论 | 状态 | 证据 |
|---|---|---|
| Mock 场景 S1–S17（必选） | 已验证（L2–L3） | 15 pass、2 skipped；`e2e/reports/m3-mock.json` |
| 只读链路不写入 | 已验证（L5） | 30 分钟、1799/1799 `measured`、`writes=0` |
| Mock ROS 租约/安全/action/停止 | 已验证（L2–L3） | 5 项 `tests_ros`；CI `m5-mock` |
| 受监督单关节/夹爪运动 | 已验证，核心（L5） | 505 vs 512 pulse；夹爪 183→645 / 647→183 |
| S13 回放决策 | 已验证（L4） | 21/21；`e2e/` 报告 |
| S17 plan-only 构造 | 已验证（L1） | `test_moveit_plan_only.py` |
| 真实 MoveIt plan-only（S17） | 已验证（L5） | 2026-11-25：`SUCCESS`、8 点轨迹、0 个控制器服务端、无命令 topic |
| S14 底盘、完整 G8 | 未验证 | 未执行 |
| S7 状态过期注入、S9 运行时发布者检测 | 未验证 | 未在实机执行 |

完整报告：[ACCEPTANCE.md](ACCEPTANCE.md)。

## 7. 相关仓库

- [`jcheng-interfaces`](https://github.com/JCHENG-Robotics/jcheng-interfaces) — 契约与哈希。
- [`jcheng-manipulation`](https://github.com/JerichoCheng/jcheng-manipulation) — 冻结的 `v1.0.0` 基线与设计记录。
- [`jcheng-ops`](https://github.com/JCHENG-Robotics/jcheng-ops) — 健康巡检与恢复。
- [`.github`](https://github.com/JCHENG-Robotics/.github) — 生态路线图、变更日志与验收。

## 8. 许可

许可证审查进行中；本仓库没有 `LICENSE`，默认版权保留。
详见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
