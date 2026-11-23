# 架构摘要（M1 指针）

> 完整设计见 `jcheng-manipulation` 分支 `design/ecosystem-v1`：
> `docs/ecosystem/04-system-architecture.md`（提交 `660b71e`）。
> 本文件只固定 M1 需要的最小事实，M3 再写实现级文档。

## 平面与域

| 域 | 名称 | 成员 |
|---|---|---|
| 0 | 厂商域 | 厂商 ROS 节点；只有硬件桥双上下文加入 |
| 41 | 特权运动域 | `jcheng_hardware`、`jcheng_safety`、`jcheng_motion` 执行器、`jcheng_guard`、`jcheng_telemetry` |
| 42 | 能力域 | `jcheng_motion` 前端、`jcheng_authority`、`jcheng_gateway`、规划、遥操作、学习、应用 |
| 43 | 仿真域 | `jcheng_testkit`、Mock 厂商、CI |

## 单一路径

```text
能力模块 -> AcquireLease -> ExecuteMotion
          -> 契约/租约/能力/幂等
          -> ValidateMotion（安全）
          -> 执行器 -> jcheng_hardware -> 厂商
          -> 总线实测 -> MotionResult/证据
```

## M1 不实现

- 不建立 ROS 包、不写节点、不写 launch、不写部署。
- 不定义契约；等待 `jcheng-interfaces` 的审计与 v0.1。
- 不连接实机；实机阶段需要单独授权。

## 关键约束

- 唯一硬件发布者：`jcheng_hardware`。
- MoveIt 只规划；执行归仲裁器。
- 安全节点缺失即锁存（fail-closed）。
- 停止中止在飞执行；未知结果锁存。
