# 架构摘要（M3/M4）

> 完整目标设计见 `jcheng-manipulation` 的
> `docs/ecosystem/04-system-architecture.md`。本文件记录 M3 Mock 与 M4 只读结构。

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

## 1.1 M4 只读组合

```text
域 0 厂商节点
  ├─ /controller_manager/joint_states → commanded
  ├─ /ros_robot_controller/battery
  ├─ 相机/TF 头信息（只记录时间戳）
  └─ GetBusServoState（get_position=1，ID 1..6 递增）
        → jcheng_telemetry
            ├─ TelemetryCache（measured vs commanded）
            ├─ 只读安全视图（DISARMED / STOP_LATCHED）
            ├─ /jcheng/telemetry/state
            ├─ /jcheng/telemetry/safety
            └─ 127.0.0.1 只读 HTTP 网关（GET only）
```

只读桥在 `jcheng_hardware/readonly.py`，没有 publish/write API；ROS 节点只
创建 `RobotState`/`SafetyState` 发布者和 `GetBusServoState` 客户端。M4 不运行
运动、租约或使能节点。域 41/42 的配置已准备，域 0→41/42 桥接留到 M5。

## 1.2 M5.1 ROS 传输（Mock）

```text
/jcheng/authority/*_lease ─┐
/jcheng/safety/* ──────────┼→ jcheng_runtime → MotionArbiter → MockHardwareBridge
/jcheng/motion/execute ────┘                    └→ EvidenceStore
/safety_stop=true ─────────→ MotionArbiter.stop → STOP_LATCHED
```

`jcheng_runtime` 是 M5.1 的单一运行时进程，暴露公共 service/action；核心
仍是 `jcheng_system_core`。M5.1 不导入厂商命令消息、不发布 `/servo_controller`、
不打开设备。M5.2 才把 Mock 硬件替换为 `/servo_controller` 硬件桥。

## 2. 包职责

| 包 | M3 职责 | M4 状态 |
|---|---|---|
| `jcheng_system_core` | 所有 Mock 逻辑 | 复用，不依赖 ROS |
| `jcheng_safety` | 安全状态诊断 | 未变；服务留到 M5 |
| `jcheng_authority` | 租约诊断 | 未变；租约服务留到 M5 |
| `jcheng_motion` | 仲裁诊断 | 未变；action 留到 M5 |
| `jcheng_hardware` | Mock 状态 | 增加只读总线桥 `readonly.py` |
| `jcheng_gateway` | HTTP 网关 | Mock 网关未变；只读网关在 `jcheng_telemetry` |
| `jcheng_guard` | 白名单诊断 | 未变；运行时检测留到 M5 |
| `jcheng_testkit` | S1–S17 | 未变 |
| `jcheng_telemetry` | — | 新增：遥测、只读网关、预检、证据、短时探针 |
| `jcheng_runtime` | — | M5.1 新增：租约/安全/校验服务、`ExecuteMotion` action（Mock） |
| `jcheng_bringup` | Mock launch | 增加 `readonly.launch.py`、`motion_mock.launch.py` |

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
