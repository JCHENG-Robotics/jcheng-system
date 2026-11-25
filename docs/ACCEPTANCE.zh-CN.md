# 验收分层

> 本文件是 v0.3.0-dev 之前的中文验收记录，保留作参考。
> 当前英文主文档见 [../ACCEPTANCE.md](../ACCEPTANCE.md)。

| 层级 | 名称 | 环境 | 实机 | M3/M4/M5/M6 状态 |
|---|---|---|---|---|
| L0 | 契约/静态 | runner | 否 | 通过 |
| L1 | 单元 | runner | 否 | 28 项 + M5.1 5 项 ROS + S13 |
| L2 | Mock 集成 | 进程内 | 否 | 通过 |
| L3 | Mock E2E | Mock 网关/总线 | 否 | 通过 |
| L4 | 回放 | 录制数据 | 否 | S13 21/21 决策一致 |
| L5 | 实机 | 域 0/41/42 | 是 | M4 只读、M5.2 核心通过；M6 S17 真实 MoveIt 只规划通过；G8 未开始 |

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

## M5.2 结果（实机，2026-11-24，核心通过）

- 前置：`DISARMED` → `ARMED_IDLE`、租约 granted、`ValidateMotion=approved`。
- 单关节 joint1 +0.05 rad：`COMPLETED`、`bus_verified=true`；
  总线 505 vs 目标 512（7 pulses，容差 8）。
- 夹爪闭 183→645（目标 700）、开 647→183（目标 170）：
  均 `COMPLETED`、`bus_verified=true`。夹爪采用基线语义：定时完成 +
  总线证据 + 方向/最小位移，不要求精确 pulse。
- 停止：`ABORTED`/`STOP_LATCHED`，hold 0.266 ms，客户端 48.4 ms。
- 停止运行时后 action server 不存在，新命令被拒。
- 无租约 `NO_LEASE`、越限 `JOINT_LIMIT` 均拒绝。
- 残余：S7 状态过期注入、S9 运行时发布者检测未单独实机验证。
- 证据：`jcheng-manipulation/docs/ecosystem/evidence/m52-final-20261124/`。

## M6 结果（S13 离线 + S17 真实 MoveIt 只规划，2026-11-24/25）

- `g8_acceptance_20261123/g8_run.json`：11/11 条命令 decision 一致。
- `studio_g8_fast/20261121/run2.json`：10/10 条命令一致。
- 越限差异仅为 profile 四位小数与基线精确值差（最大 5.9e-5 rad）；
  回放边界归一化 ≤1e-3 rad 并记录，未修改运行时安全校验。
- `tests/test_replay.py`：合成 valid/invalid 用例通过。
- `jcheng_moveit/plan_only.py`：构造/校验 `plan_only=true`，拒绝 execute/trajectory；
  `tests/test_moveit_plan_only.py` 通过。
- 真实 MoveIt：2.5.10 arm64、vendor 配置 0.3.0、domain 42、CycloneDDS、
  OMPL RRTConnect；启动 `allow_trajectory_execution:=false`。
- 单个 `MoveGroup` goal（group `arm`、自带 start、target `joint1=0.10`、
  `plan_only=true`）返回 `SUCCESS`、8 点轨迹、规划 0.050016 s。
- 无执行路径：`/arm_controller`、`/gripper_controller` 的
  FollowJointTrajectory action 各 0 个服务端；无命令 topic；未发送
  `/execute_trajectory` goal。
- 厂商配置要求 TRAC-IK，但 arm64 无包；本地副本改用 KDL +
  `position_only_ik=true`（原文件保留为 `kinematics.yaml.trac-ik`）。
- **未执行运动**；S14 实机底盘、G8 实机及 MoveIt 轨迹执行待授权。
- 证据：`jcheng-manipulation/docs/ecosystem/evidence/m6-s17-20261125/`。

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
- M5.2 核心实机验收通过；S7 状态过期注入、S9 运行时发布者检测仍未验证。
- M6 S17 真实 MoveIt 只规划通过；使用 KDL（arm64 无 TRAC-IK 包），
  轨迹执行、S14 底盘与 G8 全流程仍未验证。
- M4 遥测运行在厂商域 0；域 0→41/42 桥接尚未实现。
- 没有急停/抱闸/独立编码器/自碰撞模型。
- 底盘 `open_loop`，没有里程计。
- 许可证审查 pending；没有 `LICENSE`。
