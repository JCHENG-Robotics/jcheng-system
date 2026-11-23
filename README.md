# jcheng-system

JCHENG Robotics 的特权运行时与系统集成点。

> 状态：**v0.1.0 — M3 纯 Mock 系统已完成（2026-11-23）。**
> 本版本验证安全、租约、仲裁、Mock 硬件、HTTP 网关、guard 与 S1–S17 场景；
> **不包含 ROS service/action 传输、真实硬件、真实运动或 `LICENSE`。**

## 1. 当前交付

| 包 | 作用 |
|---|---|
| `jcheng_system_core` | 纯 Python Mock 核心：配置、安全状态机、租约、仲裁、Mock 硬件、guard、网关、证据、场景 |
| `jcheng_safety` | 安全诊断 CLI |
| `jcheng_authority` | 租约诊断 CLI |
| `jcheng_motion` | 仲裁器诊断 CLI |
| `jcheng_hardware` | Mock 硬件状态 CLI |
| `jcheng_gateway` | HTTP 网关入口 |
| `jcheng_guard` | 发布者白名单诊断 |
| `jcheng_testkit` | S1–S17 场景运行器 |
| `jcheng_bringup` | `mock_stack.launch.py` |

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
- M4 才增加 ROS service/action 传输与域 41/42/43。

## 3. 快速开始

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash

# 16 项契约/场景/网关测试
python3 -m pytest -q

# S1–S17（15 pass / 2 skipped）
ros2 run jcheng_testkit jcheng_scenarios --root . \
  --out e2e/reports/m3-mock.json

# Mock HTTP 网关（需要 token；不连接硬件）
ros2 run jcheng_gateway jcheng_gateway --port 8766 --token "<secret>"
# 或
ros2 launch jcheng_bringup mock_stack.launch.py token:="<secret>"
```

网关端点（核心子集）：`/v1/health`、`/v1/contract`、`/v1/state`、
`/v1/safety`、`/v1/leases`、`/v1/motion/validate`、`/v1/motion/execute`、
`/v1/motion/stop`、`/v1/capabilities`、`/v1/events`。

## 4. 安全边界

- 默认 `DISARMED`；使能需要操作员、检查清单与确认短语。
- 状态过期、停止锁存、profile/契约哈希不匹配、无租约、未授权发布者、
  总线失败都会拒绝或锁存。
- 停止是“软件停止”，不是急停。
- 平台没有急停/抱闸/独立编码器/自碰撞模型。
- 任何实机运动仍需单独、明确的现场授权；M3 没有实机动作。

## 5. 验收

- 15 个必选场景通过，2 个按计划跳过；16 项 pytest。
- `m1-gate`：Mock-only、无 ROS 传输、无硬件访问、无 LICENSE。
- `m3-mock`：Humble 容器构建 interfaces v0.1.0 + 9 个系统包，运行场景与
  锁文件远程校验。
- 报告：`e2e/reports/m3-mock.json`。

详见 [M3 范围](docs/M3-SCOPE.md) 与 [验收](docs/ACCEPTANCE.md)。

## 6. M4 方向

1. ROS service/action 服务器复用 Mock 核心。
2. 只读机器人接入；不写硬件。
3. 域 41/42/43、SROS2/网络隔离与 `jcheng_guard` 运行时检测。
4. 实机动作仍需单独授权。

## 7. 许可

许可证审查进行中；本仓库没有 `LICENSE`，默认版权保留。
详见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
