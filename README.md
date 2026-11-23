# jcheng-system

JCHENG Robotics 的特权运行时与系统集成点。

> 状态：**M1 骨架，未实现。**
> 本仓库当前**没有** ROS 包、没有运行时代码、没有部署脚本。
> M3 才建立 Mock 系统；M4/M5 才接入实机。

## 1. 职责（M3 起）

| 组件 | 职责 |
|---|---|
| `jcheng_bringup` | launch、启动顺序、域与参数、健康检查 |
| `jcheng_safety` | 状态看门狗、限位/轨迹/工作空间校验、停止锁存、使能状态机 |
| `jcheng_authority` | 租约、优先级、TTL、抢占、撤销 |
| `jcheng_motion` | `ExecuteMotion` 前端、意图路由、执行后端、总线确认 |
| `jcheng_hardware` | 厂商话题/服务适配；唯一硬件命令发布者 |
| `jcheng_gateway` | HTTP/WS、认证、限流、OpenAPI |
| `jcheng_telemetry` | 状态缓存、`measured|commanded` 标注、只读相机/TF 镜像、证据 |
| `jcheng_guard` | 发布者白名单、域/配置审计、违规锁存 |
| `jcheng_testkit` | Mock 厂商、故障注入、回放、场景运行器 |

## 2. M1 只允许

- 仓库说明、架构/安全/验收指针、M1 门禁。
- CI 骨架（`m1-gate`）。
- 目录占位。

**不允许：** ROS 包、Python 代码、部署单元、IDL、机器人配置、实机脚本。

## 3. 不可协商的安全边界

1. 所有实机运动只通过 `jcheng_motion` 的 `ExecuteMotion` 入口。
2. 每次执行需要有效租约 + 安全校验 + 幂等键。
3. 只有 `jcheng_hardware` 可以发布厂商命令。
4. 完成判定以总线实测为准，命令缓存只能标 `commanded`。
5. 安全状态心跳缺失即 `STOP_LATCHED`（禁止 fail-open）。
6. 停止中止在飞轨迹/底盘脉冲；底盘归零、机械臂保持。
7. 运行时不暴露原始 `set_position` / `SetBusServoState` 写路径。
8. MoveIt 只规划；执行归仲裁器。
9. `UNKNOWN` 必须锁存，总线复核后才能复位。
10. 平台没有急停/抱闸/独立编码器/自碰撞模型；软件停止不是安全认证。

## 4. 文档

- [M1 范围](docs/M1-SCOPE.md)
- [架构摘要](docs/ARCHITECTURE.md)
- [安全不变量](docs/SAFETY.md)
- [验收分层](docs/ACCEPTANCE.md)
- [运行手册占位](docs/OPERATIONS.md)

设计依据：`jcheng-manipulation` 分支 `design/ecosystem-v1` 的
`docs/ecosystem/`（提交 `660b71e`）。

## 5. 许可

许可证审查进行中；本仓库没有 `LICENSE`，默认版权保留。
详见 [LICENSE-REVIEW.md](LICENSE-REVIEW.md)。
