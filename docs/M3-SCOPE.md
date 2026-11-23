# M3 范围：纯 Mock 系统

> 状态：**已完成（2026-11-23）。**
> 目标：在不连接机器人的前提下，用 Mock 核心验证安全、租约、仲裁、执行、
> 网关、guard、锁文件与验收场景。
> **M3 不实现 ROS 服务/action 传输；M4 再接云台/真实桥。**

## 1. 交付物

| 组件 | 内容 |
|---|---|
| `jcheng_system_core` | 纯 Python Mock 核心：模型、配置、安全状态机、租约、仲裁、Mock 硬件、guard、HTTP 网关、证据、场景 |
| `jcheng_safety` | Mock 安全诊断 CLI |
| `jcheng_authority` | 租约诊断 CLI |
| `jcheng_motion` | 仲裁器诊断 CLI |
| `jcheng_hardware` | Mock 硬件状态 CLI |
| `jcheng_gateway` | HTTP 网关入口 |
| `jcheng_guard` | 发布者白名单诊断 |
| `jcheng_testkit` | S1–S17 场景运行器 |
| `jcheng_bringup` | `mock_stack.launch.py`（启动 Mock 网关） |
| `locks/ecosystem.lock.yaml` | 固定 interfaces v0.1.0、契约哈希、机器人 profile 哈希 |
| `tools/verify_lock.py` | 锁文件/契约/profile/远程 tag 校验 |

## 2. 场景结果

`ros2 run jcheng_testkit jcheng_scenarios --root .`：

- **15 passed, 2 skipped, 0 failed；required failures 0。**
- 必选：S1–S12、S14、S15、S16。
- 跳过：S13（基线回放，M6）、S17（MoveIt 只规划，M4/M6）。

| ID | 场景 | 结果 |
|---|---|---|
| S1 | 契约握手 + 单步执行 | pass |
| S2 | 契约哈希不匹配 | pass |
| S3 | 租约申请/续租/释放 | pass |
| S4 | 租约争用/抢占 | pass |
| S5 | 租约过期在飞 | pass |
| S6 | 停止锁存/复位/重新使能 | pass |
| S7 | 状态过期锁存 | pass |
| S8 | 关节限位/工作空间/桌面 | pass |
| S9 | 未授权发布者 guard | pass |
| S10 | 无租约执行 | pass |
| S11 | 幂等重放/载荷冲突 | pass |
| S12 | 总线读失败/UNKNOWN | pass |
| S13 | v1.0.0 轨迹回放 | skipped（M6） |
| S14 | 底盘脉冲/标定失效 | pass |
| S15 | 网关认证/限流/重连 | pass |
| S16 | profile 哈希不匹配 | pass |
| S17 | MoveIt 只规划 | skipped（M4/M6） |

## 3. 测试与 CI

- `pytest`：16 项（场景、租约、安全、网关、guard、配置、锁文件）。
- `m1-gate`：仓库结构 + Mock-only + 无硬件访问 + 无 LICENSE。
- `m3-mock`：Humble 容器构建 interfaces v0.1.0 + 9 个系统包，运行 pytest、
  S1–S17、锁文件远程校验与证据校验。
- 报告：`e2e/reports/m3-mock.json`（CI artifact）。

## 4. 明确不做

- 不实现 `rclpy`/`rclcpp` 节点、ROS service/action 传输（M5）。
- 不连接机器人、不打开串口、不发布厂商话题。
- 不实现真实 MoveIt 执行、真实总线、真实底盘。
- 不添加 `LICENSE`；许可证审查仍为 pending。

## 5. M4 入口（实际执行）

1. M4 先做只读接入：`jcheng_telemetry`、只读 `jcheng_hardware` 桥、只读网关、
   域 0/41/42/43 配置与预检。
2. ROS service/action 运动传输不在 M4；留到 M5 的受监督运动阶段。
3. 实机只读 30 分钟、总线对比与域隔离验证仍需单独授权。
4. 任何真实运动仍需要单独、明确的现场授权。
