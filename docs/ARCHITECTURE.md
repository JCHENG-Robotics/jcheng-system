# 架构摘要（M3）

> 完整目标设计见 `jcheng-manipulation` 的
> `docs/ecosystem/04-system-architecture.md`。本文件记录 M3 已实现的 Mock 结构。

## 1. M3 组合

```text
HTTP client
  → jcheng_gateway (bearer token, rate limit)
  → MotionArbiter
      ├─ LeaseManager        租约/优先级/抢占/TTL
      ├─ SafetySupervisor    状态机/限位/速率/工作空间/桌面/锁存
      ├─ PublisherGuard      发布者白名单
      ├─ MockHardwareBridge  Mock 总线/命令/状态/总线读失败
      └─ EvidenceStore       证据与事件
```

`jcheng_system_core` 是纯 Python 实现；ROS 包在 M3 只提供诊断 CLI、
gateway/testkit 入口和 bringup launch。**M3 不实现 ROS service/action 传输。**

## 2. 包职责

| 包 | M3 职责 | M4 方向 |
|---|---|---|
| `jcheng_system_core` | 所有 Mock 逻辑 | 复用，不依赖 ROS |
| `jcheng_safety` | 安全状态诊断 | `ValidateMotion`、`GetSafetyState`、`ResetSafety`、`ArmSystem`、`DisarmSystem` 服务 |
| `jcheng_authority` | 租约诊断 | 租约服务 |
| `jcheng_motion` | 仲裁诊断 | `ExecuteMotion` action |
| `jcheng_hardware` | Mock 状态 | 只读遥测桥 |
| `jcheng_gateway` | HTTP 网关 | 认证/限流/WS |
| `jcheng_guard` | 白名单诊断 | 运行时发布者/GID 检测 |
| `jcheng_testkit` | S1–S17 | 回放/硬件场景 |
| `jcheng_bringup` | Mock launch | 域/启动顺序 |

## 3. 安全边界（M3 已实现）

- 默认 `DISARMED`；使能需要操作员、检查清单与确认短语。
- 状态新鲜度 0.5 s；过期锁存。
- 关节限位、速度/加速度、起点、步长、时长、工作空间、桌面间隙。
- 契约/profile 哈希不匹配拒绝使能或执行。
- 无租约、错误租约、过期/抢占/撤销拒绝。
- 未授权发布者由 guard 锁存。
- 总线读失败 -> `UNKNOWN` 锁存；复位需要总线检查。
- 停止中止在飞 Mock 动作并归零底盘。

## 4. 域规划（M4）

| 域 | 内容 |
|---|---|
| 0 | 厂商节点；只有硬件桥加入 |
| 41 | 特权运动平面 |
| 42 | 能力平面 |
| 43 | 仿真/测试 |

M3 只在单进程内运行 Mock；域隔离、SROS2、容器权限在 M4 实现。

## 5. 非目标

- 没有真实 MoveIt、舵机总线、底盘电机或相机接入。
- 没有硬件急停/抱闸/独立编码器/自碰撞模型。
- 没有功能安全认证。
