# 验收分层

| 层级 | 名称 | 环境 | 实机 | 触发 |
|---|---|---|---|---|
| L0 | 契约/静态 | runner | 否 | 每次 PR |
| L1 | 单元 | runner | 否 | 每次 PR |
| L2 | Mock 集成 | 域 43 | 否 | 每次 PR |
| L3 | Mock E2E | 域 43 | 否 | PR + nightly |
| L4 | 回放 | 域 43 | 否 | nightly/发布 |
| L5 | 实机 | 域 0/41/42 | 是 | 单独授权、受监督 |

## M1

- 只要求 L0 的仓库结构门禁（`m1-gate`）。
- 没有 Mock 栈、没有单元测试、没有实机。

## M3 起

- S1–S17 场景集（契约握手、租约、停止、未授权发布者、总线失败、回放等）。
- 零非仲裁器硬件发布者。
- 安全否决/租约/幂等/停止锁存全部有测试。

## L5 前提

- Mock E2E 与回放全绿。
- 锁文件、契约哈希、profile 哈希一致。
- 预检通过；操作员与监督人确认。
- **每次实机运动单独授权。**

## 通过标准

- 运动 `COMPLETED` 且 `bus_verified=true`（底盘显式 `open_loop`）。
- 无 `UNKNOWN`；停止传播 ≤100 ms（目标）。
- G8 落点在约定门槛内（基线快路径 2.27 mm；门槛待定）。
- 证据包可被 `evidence-record` Schema 校验。
- 任何一项失败，M5/M6 不通过。

完整指标见 `jcheng-manipulation/docs/ecosystem/06-ci-cd-acceptance.md`。
