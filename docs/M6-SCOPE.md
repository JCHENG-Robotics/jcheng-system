# M6 范围：基线回放与 G8 复验

> 状态：**M6 离线：S13 回放 21/21；S17 plan-only goal 构建/校验通过；G8/底盘实机待授权。**
> 基线：`jcheng-manipulation v1.0.0`（`b3411e4`）只读引用；不复制、不修改证据。
> S13 只回放 decision，不执行运动；G8/底盘实机需单独明确授权。

## 1. 交付拆分

### 6.1 S13 轨迹回放（完成）

- `tools/replay_g8.py`：读取 v1.0.0 `g8_run.json` / `studio_g8_fast/run2.json`。
- 将基线 joint targets 转为 `JOINT_TRAJECTORY` / `GRIPPER`，通过
  `SafetySupervisor.validate` 重新判定。
- 比较 `preview.valid` 与 `approved`；允许时间戳差异；不比较物理落点。
- profile 限位为 4 位小数，基线精确值与 profile 边界差最大 5.9e-5 rad；
  回放仅在边界处做 ≤1e-3 rad 归一化并记录，不修改运行时安全校验。

结果：

| 基线 | 命令 | 一致 |
|---|---:|---:|
| `g8_acceptance_20261123/g8_run.json` | 11 | 11/11 |
| `studio_g8_fast/20261121/run2.json` | 10 | 10/10 |

### 6.2 S17 MoveIt 只规划（离线构建/校验已通过）

- `jcheng_moveit/plan_only.py`：构造 MoveGroup/GetMotionPlan 形状的 goal，
  强制 `planning_options.plan_only=true`；拒绝含 `execute`/`trajectory` 的 goal。
- `tests/test_moveit_plan_only.py`：正反用例通过；`check_m6_moveit.sh` 通过。
- 真正 move_group 的 plan-only 调用与 `plan_kinematic_path` 响应尚未在实机验证。
- 任何 MoveIt 执行路径必须关闭或无硬件发布权限。

### 6.3 S14 底盘脉冲（Mock 已覆盖，实机待授权）

- Mock S14 已通过：`calibration_invalidated=true`，后续抓取拒绝。
- 实机底盘脉冲需清空工作区、确认标定失效、单独授权；本轮不做。

### 6.4 G8 全流程（待授权）

- 定位 -> 抓取 -> 抬升 -> 放回；每阶段总线确认；无 `UNKNOWN`。
- 落点门槛：基线快路径 2.27 mm；建议 ≤5 mm（需确认）。
- 需要相机、MoveIt、旧网关只读回滚、操作员/监督人；单独授权。
- M6 不修改 `jcheng-manipulation v1.0.0` 的 G8 运行器或证据。

## 2. 验收

### 已完成（离线）

- S13 回放 21/21 决策一致。
- `tests/test_replay.py` 合成 valid/invalid 用例通过。
- S17 plan-only goal 构建/校验与正反测试通过。
- 无运动、无硬件连接。

### 待完成

- S17 plan-only 在真实 MoveIt/move_group 环境验证。
- S14 实机底盘脉冲/标定失效。
- G8 全流程实机复验与落点证据。
- 旧网关回滚演练。
- 性能指标：校验/执行/停止延迟（停止 0.266 ms；S13 校验 mean 0.0166 ms、
  p95 ≤0.0352 ms；真实执行延迟待 M6 实机）。

## 3. 非目标

- 不修改 v1.0.0 的标签、证据或实验历史。
- 不复制基线证据；回放只读引用。
- 不把 MoveIt 接到硬件执行；只规划。
- 不声称功能安全认证。
