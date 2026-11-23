# M3 场景目录

场景实现位于 `ros2_ws/src/jcheng_system_core/jcheng_system_core/scenarios.py`。
运行：

```bash
ros2 run jcheng_testkit jcheng_scenarios --root . --out e2e/reports/m3-mock.json
```

| ID | 名称 | 层 | M3 状态 |
|---|---|---|---|
| S1 | 契约握手 + 单步执行 | L2/L3 | pass |
| S2 | 契约哈希不匹配 | L2 | pass |
| S3 | 租约生命周期 | L2 | pass |
| S4 | 租约争用/抢占 | L2/L3 | pass |
| S5 | 租约过期在飞 | L3 | pass |
| S6 | 停止锁存/复位 | L3 | pass |
| S7 | 状态过期 | L2 | pass |
| S8 | 限位/工作空间/桌面 | L2/L3 | pass |
| S9 | 未授权发布者 | L3 | pass |
| S10 | 无租约执行 | L2/L3 | pass |
| S11 | 幂等 | L2 | pass |
| S12 | 总线读失败/UNKNOWN | L3 | pass |
| S13 | v1.0.0 轨迹回放 | L4 | M6 |
| S14 | 底盘脉冲/标定失效 | L3 | pass |
| S15 | 网关认证/限流/重连 | L3 | pass |
| S16 | profile 哈希不匹配 | L2 | pass |
| S17 | MoveIt 只规划 | L2 | M4/M6 |
