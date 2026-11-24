# 验收分层

| 层级 | 名称 | 环境 | 实机 | M3/M4/M5 状态 |
|---|---|---|---|---|
| L0 | 契约/静态 | runner | 否 | 通过 |
| L1 | 单元 | runner | 否 | 28 项 + M5.1 5 项 ROS |
| L2 | Mock 集成 | 进程内 | 否 | 通过 |
| L3 | Mock E2E | Mock 网关/总线 | 否 | 通过 |
| L4 | 回放 | 录制数据 | 否 | S13 M6 |
| L5 | 实机 | 域 0/41/42 | 是 | M4 只读通过；M5 实机运动未开始 |

## M3 结果

- `ros2 run jcheng_testkit jcheng_scenarios --root .`：
  **15 passed, 2 skipped, 0 failed；required failures 0**。
- 必选：S1–S12、S14、S15、S16 全部通过。
- 跳过：S13（M6 回放）、S17（M4/M6 MoveIt）。
- `pytest`：16 项通过。
- `verify_lock.py`：interfaces v0.1.0、契约哈希、profile 哈希、远程 tag 一致。
- CI：Humble `m3-mock` 构建 interfaces + 9 个系统包并运行上述检查。

## M4 结果（离线）

- `pytest`：28 项（27 passed / 1 conditional skip）；新增只读缓存、桥、网关、
  预检、证据与短时探针测试。
- `jcheng_readonly_probe`：短时 Mock 运行 `writes=0`、`measured_samples>0`、
  preflight `ok=true`。
- `scripts/check_m4_readonly.sh`：通过；无命令发布者、无写服务、无设备路径。
- Humble `m4-readonly`：构建 interfaces + 10 个系统包；运行 pytest、短时探针
  与只读门禁。

## M4 结果（实机只读，2026-11-23）

- 30 分钟（1798 s）：**1799/1799 样本 `truth=measured`、`verification=bus`，
  `writes=0`，`ok=true`**。
- 独立 `GetBusServoState` 读数与新节点逐项一致；旧网关 `measured` 实为
  `/controller_manager/servo_states (command cache)`，与真实总线最大相差
  387 pulses。M4 以独立总线读数为准。
- 域 42 只看到 `/parameter_events`、`/rosout`；看不到厂商命令话题。
- `/jcheng/telemetry/state` 唯一发布者为 `jcheng_telemetry`；没有命令发布者。
- 相机 `/depth_cam/rgb/image_raw` 当前无 publisher；`/tf` 健康；
  `/tf_static` 的 TRANSIENT_LOCAL QoS 尚未适配。
- **未发送任何运动命令；未重启厂商容器。**
- 证据：`jcheng-manipulation/docs/ecosystem/evidence/m4-readonly-20261123/`。

## M5.1 结果（Mock ROS 传输，2026-11-24）

- `tests_ros`：5 项通过（服务往返、租约、安全 arm、停止锁存、action 完成与
  总线确认）。
- `scripts/check_m5_mock.sh`：通过；运行时只使用 `MockStack`，无厂商命令
  发布者、无写服务、无设备路径。
- `m5-mock`：Humble 构建 interfaces + 11 个系统包，运行 `pytest` 与
  `pytest tests_ros`。
- **没有连接机器人、没有发送运动命令。**

## M5.2 结果（实机，2026-11-24，部分通过）

- 前置：`DISARMED` → `ARMED_IDLE`、租约 granted、`ValidateMotion=approved`。
- 单关节 joint1 +0.05 rad：`COMPLETED`、`bus_verified=true`；
  总线 505 vs 目标 512（7 pulses，容差 8）。
- 夹爪全开 2.22 rad（170 pulse）：总线 183，差 13 > ±8；
  `MOTION_OUTCOME_UNKNOWN`，`STOP_LATCHED`；闭合被安全拒绝。
- 第二次授权后闭合 0.25 rad（640 pulse）：总线 620/619，差 20/21；
  再次 `STOP_LATCHED`；开动作未发送。
- 第三次内侧开目标 2.12 rad（194 pulse）：总线 207，差 13；
  再次 `STOP_LATCHED`；闭合未发送。
- 实际可达行程约 183–619 pulses，窄于名义 170–700；三次均提前停住。
- 未自动重试、未重置；没有继续运动。
- 最终总线：183/500/110/831/597/507（servo 1..6）。
- 结论：**M5.2 未完成**；需人工检查夹爪开限位/连杆，确认可达开度后再授权。
- 证据：`jcheng-manipulation/docs/ecosystem/evidence/m52-20261124/`。

## 通过标准（M3）

- Mock 场景 required failures 为 0。
- 零非 `jcheng_hardware` 发布者（S1/S10）。
- 停止、状态过期、限位、工作空间、桌面、guard、总线失败均拒绝或锁存。
- 无租约/错误契约/错误 profile 均拒绝。
- 无实机连接、无真实运动、无硬件写入。

## L5 前提（未来）

- L0–L4 全绿。
- 锁文件、契约哈希、profile 哈希一致。
- 预检通过；操作员与监督人确认。
- **每次实机运动单独授权。**

## 已知限制

- M3 是 Mock；M4 只读接入已在机器人上通过。
- M5.1 的 service/action 只作用于 Mock 硬件；M5.2 已上实机但夹爪开未通过。
- M4 遥测运行在厂商域 0；域 0→41/42 桥接尚未实现。
- M5.2 的停止传播 ≤100 ms、节点停止后拒绝新命令尚未实机验证。
- 没有真实 `ExecuteMotion` 执行、真实单关节/夹爪运动。
- 没有急停/抱闸/独立编码器/自碰撞模型。
- 底盘 `open_loop`，没有里程计。
- 许可证审查 pending；没有 `LICENSE`。
