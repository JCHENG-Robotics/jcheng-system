# Changelog

## Unreleased — M4 read-only + M5.1 Mock ROS transport

- 新增 `jcheng_hardware/readonly.py`：递增舵机 ID、0–1000 脉冲校验、
  无 publish/write API 的只读总线桥。
- 新增 `jcheng_telemetry`：遥测缓存、`measured/commanded` 区分、只读安全视图、
  只读 HTTP 网关、预检、证据与短时 Mock 探针。
- 新增 `readonly.launch.py`：只启动 telemetry 节点；不启动厂商容器或运动节点。
- 新增域 0/41/42/43 配置、CycloneDDS 单播模板、只读允许/禁止列表。
- 新增 `scripts/check_m3_gate.sh`（冻结 M3 包集）与
  `scripts/check_m4_readonly.sh`（只读门禁）；CI 增加 `m4-readonly`。
- 2026-11-23 实机只读验收通过：30 分钟、1799/1799 `measured`、`writes=0`；
  独立总线读数与新节点一致；旧网关 `measured` 实为命令缓存，与真实总线
  最大相差 387 pulses。没有发送任何运动命令。
- 修复：总线默认 4 Hz；网关 token 改走环境变量；关闭时避免重复
  `rclpy.shutdown`。
- 新增 `jcheng_runtime`：M5.1 ROS 服务/action 传输，租约、安全、校验、
  停止锁存；只使用 `MockStack`，没有厂商命令发布者。
- 新增 `tests_ros` 5 项与 `scripts/check_m5_mock.sh`；CI 增加 `m5-mock`。
- 新增 `jcheng_vendor` 与 `motion_vendor.launch.py`：唯一 `/servo_controller`
  发布者、单关节/夹爪、`GetBusServoState` 复核、停止锁存/保持。
- 2026-11-24 实机：joint1 +0.05 rad `COMPLETED`（505 vs 512，7 pulses）；
  夹爪 170→183、194→207、640→620/619，三次 `UNKNOWN` 锁存。
  未自动重试；M5.2 未完成，需人工检查夹爪开/闭限位、连杆、伺服 horn
  后再授权。

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
