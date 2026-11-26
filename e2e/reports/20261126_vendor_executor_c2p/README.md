# M5.2 HardwareExecutor C2p fix — evidence

> Date: 2026-11-26. Level: **L2 offline** (fake bridge; no ROS, no device, no
> hardware). Target decision: M4/M5 use the `jcheng_system_core` gateway with
> the M5.2 `jcheng_vendor` executor, not the frozen `armpi_debug_gateway`.

## 1. Finding

`HardwareExecutor.execute` set `_active` and cleared the stop event under
`_active_lock`, then released the lock and called `bridge.pulse_snapshot()` and
`bridge.send_step()` outside it. `stop()` set the stop event, latched safety
and published the hold without that lock.

A stop in that window produced:

```text
execute: snapshot
stop:             hold
execute:          send_step     <-- command published after the stop
execute:                        hold
```

The result was `ABORTED`, but the command had already been sent after the stop;
the hold was not the last command. This is the same C2p ordering defect as the
original mock gateway, in the chosen hardware executor.

## 2. Fix (R8)

`send_step`/`send_gripper` and the stop's `set + latch + hold` now share
`_active_lock`:

```text
execute: pulse_snapshot -> [lock: stop? abort : send] -> wait
stop:                      [lock: set stop, latch, hold]
tick lease loss:           [lock: set stop, latch, hold]
```

- Stop before the send: the lock check sees the stop, publishes no command and
  returns `ABORTED`.
- Stop after the send: the hold waits for the send and therefore follows it.
- The wait and bus read remain outside the lock.

## 3. Tests

`tests/test_vendor_executor_c2p.py`:

| Case | Result |
|---|---|
| stop while the execute thread is paused before the send | `ABORTED`; no `send_step`; events `snapshot, hold, hold` |
| stop after the send | `ABORTED`; `send_step` before `hold` |
| normal step | `COMPLETED`, `bus_verified=true`, no hold |

The pre-fix failure is recorded in [`pre-fix.txt`](pre-fix.txt); post-fix output
is [`pytest.txt`](pytest.txt), gate is [`gate.txt`](gate.txt).

## 4. Verification

```text
python3 -m pytest -q       pass (no failures)
scripts/check_repo_gate.sh OK
```

## 5. Limitations

- Fake bridge, not the vendor ROS bridge. It proves the executor's ordering and
  target verification logic, not the servo bus or a physical stop.
- `pytest.ini` now includes the `jcheng_vendor` package path so the offline
  suite can import the ROS-free executor module.
- A hardware C1–C5 session on the chosen gateway is still required; this does
  not replace it. No device was opened and no command was published.
- The frozen `armpi_debug_gateway` is not the target; its R7 race remains a
  separate legacy risk.
