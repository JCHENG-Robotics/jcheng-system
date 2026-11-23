# Changelog

## 0.1.0 (2026-11-23) — M3 Mock

- 建立纯 Python `jcheng_system_core`：安全状态机、租约、仲裁、Mock 硬件、
  guard、HTTP 网关、证据与 S1–S17 场景。
- 建立 9 个 ROS 包：core、safety、authority、motion、hardware、gateway、
  guard、testkit、bringup。
- 15 个 M3 场景通过，2 个按计划跳过；16 项 pytest。
- 建立 `locks/ecosystem.lock.yaml` 与 `verify_lock.py`，固定 interfaces v0.1.0。
- 建立 Humble `m3-mock` CI 与 Mock-only 仓库门禁。
- 不包含 ROS service/action 传输、真实硬件、真实运动或 LICENSE。

## 版本规则

系统版本与契约版本分离；`major` 表示 API/租约/安全语义不兼容，
`minor` 表示新增能力/profile，`patch` 表示缺陷/文档修复。
详见设计文档 `05-versioning-lock-release.md`。
