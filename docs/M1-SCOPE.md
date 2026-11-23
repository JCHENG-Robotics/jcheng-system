# M1 范围

## 允许

- 仓库说明、架构摘要、安全不变量、验收分层、运行手册占位。
- CI 骨架（`m1-gate`）。
- 目录占位（`ros2_ws/src`、`configs`、`deploy`、`e2e`、`locks`、`tools`）。
- 许可证审查状态说明。

## 不允许

- ROS 包、Python 代码、C++ 代码。
- 部署单元、Dockerfile、机器人配置、launch 文件。
- `.msg/.srv/.action` 或任何契约定义。
- 实机脚本、硬件话题发布者、串口/设备访问。
- 修改 `jcheng-manipulation` 的核心代码、标签、证据或实验历史。

## 出口条件

- `scripts/check_m1_gate.sh` 通过。
- CI `m1-gate` 通过。
- 没有 `LICENSE`（许可证审查 pending）。
- 没有实机连接或运动命令。

## M3 入口

- `jcheng-interfaces` 发布契约版本并通过契约审计。
- Mock 栈设计评审（安全、租约、仲裁、网关、guard）。
- 锁文件 schema 与验证工具。
- S1–S17 场景集。
