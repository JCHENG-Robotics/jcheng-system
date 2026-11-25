# M5 范围：受监督单关节/夹爪执行

> 状态：**M5.2 核心实机验收通过（2026-11-24）；此前夹爪三次精确判据失败，S7/S9 仍有残余。**
> 单关节、夹爪开/合、软件停止、停止运行时后拒绝、无租约/越限均验证；
> 夹爪采用“定时完成 + 总线证据 + 方向/最小位移”，手臂仍 ±8 pulse。
> 基线：`jcheng-manipulation v1.0.0`（`b3411e4`）只读引用；`v0.1.0` 标签不动。

## 1. 交付拆分

### M5.1 ROS 传输（本次，Mock-only）

| 组件 | 内容 |
|---|---|
| `jcheng_runtime` | 单一运行时节点：租约、安全、校验、`ExecuteMotion` action、能力清单 |
| 服务 | `/jcheng/authority/{acquire,renew,release,revoke}_lease`、`/jcheng/safety/{get_state,validate_motion,arm,disarm,reset}`、`/jcheng/capabilities` |
| Action | `/jcheng/motion/execute`（`jcheng_interfaces/action/ExecuteMotion`） |
| 停止输入 | `/safety_stop`（`std_msgs/Bool`）→ `MotionArbiter.stop`，锁存并拒绝新命令 |
| 硬件 | `MockStack`；**没有厂商命令发布者、没有写服务、没有串口/设备访问** |
| 测试 | `tests_ros/test_runtime.py`：服务、租约、校验、停止锁存、action 完成 |

### M5.2 硬件执行（已实现；核心实机通过）

- `jcheng_vendor` 是 `/servo_controller`（`servo_controller_msgs/ServosPosition`）的
  唯一发布者；只允许 step/gripper 目标，发布前必须通过安全校验与租约。
- 手臂完成判定：`GetBusServoState` 目标 ±8 pulse。
- 夹爪完成判定：基线语义——插值时间完成 + 总线读数证据 + 方向/最小位移
  ≥5 pulse；夹爪会接触/停位，不要求精确 pulse。
- **实机结果（2026-11-24）：**
  - 单关节 joint1 +0.05 rad：`COMPLETED`，505 vs 512（7 pulses）。
  - 夹爪闭 183→645（目标 700）、开 647→183（目标 170）：均 `COMPLETED`、
    `bus_verified=true`。
  - 历史：改用基线语义前，夹爪三次按 ±8 pulse 精确判据尝试：
    170→183、194→207、640→620/619，均 `UNKNOWN`/`STOP_LATCHED`，未自动重试；
    实际可达行程约 183–619 pulses，窄于名义 170–700。
  - 停止：`ABORTED`/`STOP_LATCHED`，hold 0.266 ms，客户端 48.4 ms。
  - 停止运行时后 action server 不存在，新命令被拒。
  - 无租约 `NO_LEASE`、越限 `JOINT_LIMIT` 均拒绝。
  - 残余：S7 状态过期注入、S9 运行时发布者检测未单独实机验证。
- 任何下一次实机动作仍需单独、明确授权。

## 2. 服务/action 名称（M5.1）

```text
/jcheng/authority/acquire_lease
/jcheng/authority/renew_lease
/jcheng/authority/release_lease
/jcheng/authority/revoke_lease
/jcheng/safety/get_state
/jcheng/safety/validate_motion
/jcheng/safety/arm
/jcheng/safety/disarm
/jcheng/safety/reset
/jcheng/capabilities
/jcheng/motion/execute              (action)
/safety_stop                        (std_msgs/Bool, true = stop)
```

## 3. M5.1 安全边界

- 默认 `DISARMED`；使能需要检查清单与确认短语。
- 所有运动必须携带有效租约；无租约/过期/抢占/撤销都拒绝。
- `ValidateMotion` 只校验，不执行、不发布命令。
- `ExecuteMotion` 是唯一运动入口；M5.1 只作用于 Mock 硬件。
- `/safety_stop=true` 立即 `STOP_LATCHED`，丢弃在飞 Mock 动作；新校验/运动被拒。
- 复位需要确认短语；`MOTION_OUTCOME_UNKNOWN` 需要总线检查后才能清除（核心逻辑）。
- M5.1 **没有** `servo_controller_msgs`/`ros_robot_controller_msgs` 导入，
  没有 `/servo_controller` 发布，没有串口/设备路径。

## 4. 测试与 CI

- `tests_ros`：服务往返、租约、校验、停止锁存、复位、action 完成与总线确认。
- 默认 `tests`：M3/M4 回归保持通过。
- `scripts/check_m5_mock.sh`：M5.1 Mock-only 静态门禁。
- CI `m5-mock`：Humble 构建 11 包 + 运行 `tests` 与 `tests_ros` + 门禁。

## 5. 明确不做

- 不连接机器人、不发布 `/servo_controller`、不写总线、不打开串口。
- 不实现 M5.2 硬件桥、不运行 G8、不做 MoveIt 执行。
- 不声称功能安全认证；没有急停/抱闸/独立编码器/自碰撞模型。
- 不修改 `jcheng-manipulation v1.0.0` 的标签、证据或实验历史。

## 6. 回滚

M5.1 停止 `jcheng_runtime` 即可；无硬件写入、无厂商容器影响。
M5.2 回滚沿用 M4 只读栈与旧网关（旧网关保持只读）。
