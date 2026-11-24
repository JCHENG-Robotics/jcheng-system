# 安全不变量（M3/M4 实现）

> 这些不是建议，是后续实现与验收的门槛。M3 已在 Mock 核心中实现并由
> S1–S16 验证；M4 增加了只读真实接入，**仍不发送任何运动命令**。

## 1. 唯一入口

- 实机运动只通过 `ExecuteMotion`；M3 中对应 `MotionArbiter.execute`。
- 能力模块只提交意图，不发布厂商话题、不打开串口、不加入硬件域。
- MoveIt 只规划；M3 没有 MoveIt 传输。

## 2. 控制权

- 每次执行需要有效租约；作用域 `arm|gripper|base|full`。
- 租约 TTL、优先级、抢占、撤销可审计；S3/S4/S5 覆盖。
- 进程重启/配置变化/安全锁存/总线未知：租约立即失效。
- `UNKNOWN` 后不得自动重试运动；S12 覆盖。

## 3. 安全校验（M3 已实现）

- 状态新鲜度 0.5 s、关节齐全、有限、位置限位。
- 轨迹时间递增、时长/点数、速度/加速度。
- 起点 0.08 rad；单步/pose 步长。
- 工作空间、桌面间隙（POSE）；
- 契约/profile 哈希；租约；guard。
- 校验不能被调用方放宽。

## 4. 真值与完成

- Mock 总线读数是完成判定的唯一依据。
- `/joint_states`/缓存语义在真实系统中标 `commanded`；M3 使用 `truth=measured`。
- `COMPLETED` 必须有总线确认（底盘 `open_loop` 除外）。
- 读失败/超时：`UNKNOWN` 锁存。

## 5. 停止

- 安全心跳缺失/过期：`STOP_LATCHED`，不得 fail-open。
- 停止中止在飞动作；底盘归零、机械臂保持。
- 停止需要操作员认证，但不需要租约。
- 软件停止不是急停；UI/文档不得写“急停”。

## 6. 禁止的写路径

- 原始总线 `set_position`。
- `SetBusServoState`（ID/offset/限位/力矩/EEPROM）。
- 直接 `/arm_controller/follow_joint_trajectory`/`/move_action` 执行。
- `/grasp`、手柄、`/cmd_vel` 等厂商直发路径。
- 任何绕过 `jcheng_motion`/`jcheng_safety` 的调试端点。

## 7. 残余风险

- 无急停、抱闸、独立编码器、力矩传感、自碰撞模型。
- 厂商启动检查会移动机械臂；M4 不自动重启厂商容器。
- 底盘开环、无里程计；任何底盘运动使桌面标定失效（S14 标注）。
- 软件层不能替代断电/物理隔离。

## 8. M4 只读不变量

- 只发布 `/jcheng/telemetry/state` 与 `/jcheng/telemetry/safety`；没有命令发布者。
- 只调用 `GetBusServoState`，只设置 `get_position=1`；舵机 ID 1..6 严格递增；
  不读 offset/EEPROM/力矩，不写任何寄存器。
- 不打开 `/dev/tty*`、不导入 `serial`、不访问串口设备。
- `/controller_manager/joint_states` 只标记 `commanded`；只有新的总线样本标记
  `measured`，且 `verification=bus`。
- 只读网关只有 GET；非 GET 在任何路由前返回 403；没有 motion/lease/arm/reset 路由。
- 只读安全视图只会是 `DISARMED` 或 `STOP_LATCHED`，不是控制权威；不使能、不租约。
- M4 运行在域 0；域 41/42 配置已准备，但不能把运动节点放进域 0。

## 9. M5.1 ROS 传输不变量

- 唯一运动入口是 `/jcheng/motion/execute`（`ExecuteMotion` action）。
- `ValidateMotion` 只校验，不执行、不发布命令。
- 所有执行必须携带有效租约；无租约/过期/抢占/撤销拒绝。
- `/safety_stop=true` 立即 `STOP_LATCHED`，新校验/运动被拒。
- 使能需要检查清单与确认短语；默认 `DISARMED`。
- M5.1 只使用 `MockStack`：不导入厂商命令消息、不发布 `/servo_controller`、
  不打开设备。M5.2 才允许受审查的 `/servo_controller` 硬件桥。
- M5.1 的 action 成功必须来自 Mock 总线确认；不得把命令缓存当实测。

## 10. M3 验证

| 场景 | 结果 |
|---|---|
| S5 租约过期中止 | pass |
| S6 停止锁存/复位 | pass |
| S7 状态过期 | pass |
| S8 限位/工作空间/桌面 | pass |
| S9 未授权发布者 | pass |
| S12 总线读失败/UNKNOWN | pass |
| S14 底盘脉冲/标定失效 | pass |
| S16 profile 哈希 | pass |
