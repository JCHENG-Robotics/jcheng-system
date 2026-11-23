# 安全不变量（M1 冻结）

> 这些不是建议，是后续实现与验收的门槛。任何一条不满足，相关实机能力不得启用。
> 完整分析见 `jcheng-manipulation/docs/ecosystem/04-system-architecture.md`。

## 1. 唯一入口

- 实机运动只通过 `ExecuteMotion`。
- 能力模块只提交意图，不发布厂商话题、不打开串口、不加入硬件域。
- MoveIt 只规划；不启动控制器执行链路。

## 2. 控制权

- 每次执行需要有效租约；作用域 `arm|gripper|base|full`。
- 租约 TTL、优先级、抢占、撤销可审计。
- 进程重启/配置变化/安全锁存/总线未知：租约立即失效。
- 死守：`UNKNOWN` 后不得自动重试运动。

## 3. 安全校验

- 状态新鲜度、关节齐全、位置限位、速度/加速度、起点、
  时长/点数、工作空间、桌面间隙。
- 校验不能被调用方放宽（起点、速率、阈值）。
- 配置/profile/契约哈希不匹配：拒绝使能。

## 4. 真值与完成

- 总线实测（`GetBusServoState`）是完成判定的唯一依据。
- `/joint_states`/`servo_states` 是命令缓存，只能标 `commanded`。
- `COMPLETED` 必须有 `bus_verified=true`（或显式允许的 `open_loop`）。
- 读失败/超时/网络失败：`UNKNOWN` 锁存。

## 5. 停止

- 安全心跳缺失/过期：`STOP_LATCHED`，不得 fail-open。
- 停止中止在飞轨迹/底盘脉冲；底盘归零、机械臂保持、夹爪保持。
- 停止需要操作员认证，但不需要租约（任何已认证操作员都能停）。
- 软件停止不是急停；UI/文档不得写“急停”。

## 6. 禁止的写路径

- 原始 `/ros_robot_controller/bus_servo/set_position`。
- `SetBusServoState`（ID/offset/限位/力矩/EEPROM）。
- 直接 `/arm_controller/follow_joint_trajectory`/`/move_action` 执行。
- `/grasp`、手柄、`/cmd_vel` 等厂商直发路径。
- 任何绕过 `jcheng_motion`/`jcheng_safety` 的调试端点。

## 7. 残余风险

- 无急停、抱闸、独立编码器、力矩传感、自碰撞模型。
- 厂商启动检查会移动机械臂；不得自动重启厂商容器。
- 底盘开环、无里程计；任何底盘运动使桌面标定失效。
- 软件层不能替代断电/物理隔离。
