# M4 范围：机器人只读接入

> 状态：**离线实现完成（2026-11-23）；实机 30 分钟只读验收待单独授权。**
> 只读代码、配置、门禁与 CI 已加入分支 `m4/readonly-integration`；
> **尚未连接机器人，未发送任何运动命令**。
> 基线：`jcheng-manipulation v1.0.0`（`b3411e4`）只读引用；`v0.1.0` 标签不动。

## 1. 目标

在不发送任何运动命令的前提下，把新系统接到真实厂商栈上，证明：

- 只读状态链路可用：`/controller_manager/joint_states`、电池、总线实测、相机/TF 健康。
- 总线实测只通过 `GetBusServoState` 的 `get_position` 读取，舵机 ID 严格递增。
- `/controller_manager/joint_states` 只标记为 `commanded`，不能作为完成依据。
- 只读网关只有 GET；任何运动、租约、使能、复位路径都不存在。
- 域 0/41/42/43 配置、预检、证据和 30 分钟只读验收协议就绪。

## 2. 交付物

| 组件 | M4 内容 |
|---|---|
| `jcheng_hardware/readonly.py` | 纯 Python 只读桥：递增 ID、0–1000 脉冲校验、无发布/写入 API |
| `jcheng_telemetry` | 遥测缓存、`truth=measured/commanded`、只读网关、预检、证据、短时 Mock 探针 |
| `jcheng_telemetry.node` | ROS 节点：订阅厂商状态、只调用 `GetBusServoState`、只发布 `RobotState`/`SafetyState` |
| `jcheng_bringup/launch/readonly.launch.py` | 只启动 telemetry 节点；不启动厂商容器或运动节点 |
| `configs/domains.yaml` | 域 0/41/42/43、进程归属、隔离规则、只读策略 |
| `configs/readonly.yaml` | 订阅/发布/调用允许列表；命令话题与服务禁止列表 |
| `configs/robots/armpi_ultra/readonly_mapping.yaml` | 只读脉冲→弧度映射；来源哈希；读取顺序 1..6 |
| `configs/dds/*.xml` | CycloneDDS 单播域模板 |
| `scripts/check_m4_readonly.sh` | 静态只读门禁：禁止命令发布者、写服务、设备路径 |
| CI `m4-readonly` | 构建 10 包、pytest、短时探针、只读门禁 |

## 3. 只读不变量

1. **没有命令发布者。** `jcheng_telemetry` 只允许发布
   `/jcheng/telemetry/state` 与 `/jcheng/telemetry/safety`。
2. **没有写服务。** 只允许调用
   `/ros_robot_controller/bus_servo/get_state`；禁止
   `SetBusServoState`、`SetPWMServoState`、`set_motor_speeds`。
3. **没有原始设备。** 不打开 `/dev/tty*`、不导入 `serial`、不访问串口。
4. **总线读取只读位置。** `GetBusServoCmd` 只设置 `id` 与 `get_position=1`；
   不读 offset/EEPROM/力矩；ID 按 1、2、3、4、5、6 严格递增。
5. **缓存不是实测。** `/controller_manager/joint_states` 标记
   `truth=commanded`、`verification=timed`；只有新的总线样本标记
   `truth=measured`、`verification=bus`。
6. **网关只读。** 所有非 GET 方法在任何路由前返回 403；没有 motion/lease/
   arm/reset 路由；网关只绑定 `127.0.0.1`。
7. **不使能、不租约、不运动。** 只读安全视图只会是 `DISARMED` 或
   `STOP_LATCHED`，不是控制权威。

## 4. 域拓扑（M4 实际运行）

M4 的遥测节点运行在厂商域 0，因为厂商状态话题在那里：

```text
域 0：厂商节点 + jcheng_telemetry（只读）
域 41：特权运动域配置已准备；M4 不运行运动节点
域 42：能力域配置已准备；M4 不运行能力节点
域 43：Mock/CI；不属于实机运行
```

这是 M4 的明确限制：只读 API 由域 0 内的 localhost 网关提供。M5 接入运动
平面时，必须增加域 0→41/42 的受控桥接，不能把运动节点放进域 0。

## 5. 验收

### 5.1 CI / 离线

- 全部 pytest 通过（含只读缓存、桥、网关、预检、证据）。
- `jcheng_readonly_probe` 短时 Mock 运行：`writes=0`，`measured_samples>0`。
- `scripts/check_m4_readonly.sh` 通过。
- 10 个 ROS 包在 Humble 容器构建通过。
- `m3-mock` 仍通过，证明 M4 没有破坏 M3 基线。

### 5.2 实机（待单独授权）

1. 厂商栈已运行；不重启厂商容器。
2. 有线链路、DDS 发现、时钟偏移在阈值内。
3. `jcheng_telemetry_node` 只读运行 **≥30 分钟**。
4. `truth=measured` 样本来自 `GetBusServoState`；同一时刻与旧网关脉冲读数比较，
   容差按脉冲。
5. 能力域进程看不到厂商命令话题（域隔离验证）。
6. 证据包：`writes=0`、无命令发布、无写服务调用、无 `UNKNOWN` 自动重试。
7. 全程**没有发送任何运动命令**；任何实机运动仍需单独授权。

## 6. 非目标

- 不实现 `ExecuteMotion` action、租约服务、安全服务或运动执行器。
- 不使能、不夹爪、不底盘、不 MoveIt。
- 不部署或重启厂商容器；不写 EEPROM/ID/offset/限位。
- 不声称功能安全认证；没有急停/抱闸/独立编码器/自碰撞模型。
- 不改 `jcheng-manipulation v1.0.0` 的标签、证据或实验历史。

## 7. 回滚

停止 `jcheng_telemetry` 与只读网关；旧网关继续只读；不触碰厂商容器；无运动影响。
