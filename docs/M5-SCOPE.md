# M5 范围：受监督单关节/夹爪执行

> 状态：**M5.1 已合并；M5.2 单关节通过，夹爪三次 UNKNOWN，M5.2 未完成（2026-11-24）。**
> 单关节 joint1 +0.05 rad `COMPLETED`；夹爪 170→183、194→207、640→620/619
> 均超过 ±8，已 `STOP_LATCHED`；没有自动重试。
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

### M5.2 硬件执行（已实现；实机部分通过）

- `jcheng_vendor` 是 `/servo_controller`（`servo_controller_msgs/ServosPosition`）的
  唯一发布者；只允许 step/gripper 目标，发布前必须通过安全校验与租约。
- 完成判定只来自 `GetBusServoState`；`UNKNOWN` 后不自动重试。
- **实机结果（2026-11-24）：**
  - 单关节 joint1 +0.05 rad：`COMPLETED`，总线 505 vs 目标 512（7 pulses）。
  - 第一次夹爪全开：目标 170 pulse，总线 183，差 13 > ±8；`STOP_LATCHED`。
  - 第二次夹爪闭合：目标 640 pulse，总线 620/619，差 20/21 > ±8；
    再次 `STOP_LATCHED`；开动作未发送。
  - 第三次夹爪开（内侧）：目标 194 pulse，总线 207，差 13 > ±8；
    再次 `STOP_LATCHED`；闭合未发送。
  - 三次尝试的实际行程约 183–619 pulses，窄于名义 170–700。
  - 未做停止传播/节点停止测试；M5.2 **未完成**；需人工检查夹爪
    开/闭限位、连杆与伺服 horn 后再授权。
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
