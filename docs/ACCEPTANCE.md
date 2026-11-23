# 验收分层

| 层级 | 名称 | 环境 | 实机 | M3/M4 状态 |
|---|---|---|---|---|
| L0 | 契约/静态 | runner | 否 | 通过 |
| L1 | 单元 | runner | 否 | 28 项（27 pass / 1 conditional skip） |
| L2 | Mock 集成 | 进程内 | 否 | 通过 |
| L3 | Mock E2E | Mock 网关/总线 | 否 | 通过 |
| L4 | 回放 | 录制数据 | 否 | S13 M6 |
| L5 | 实机 | 域 0/41/42 | 是 | M4 只读未实机；M5 运动未开始 |

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
- **未连接机器人；未发送任何运动命令。**
- 实机 M4：**待单独授权**；≥30 分钟只读、总线对比、域隔离尚未执行。

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

- M3 是 Mock；M4 只读接入尚未在机器人上执行。
- M4 遥测运行在厂商域 0；域 0→41/42 桥接留到 M5。
- 没有运动 service/action 执行、`ExecuteMotion` action 或真实运动。
- 没有急停/抱闸/独立编码器/自碰撞模型。
- 底盘 `open_loop`，没有里程计。
- 许可证审查 pending；没有 `LICENSE`。
