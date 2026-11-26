# Deployed gateway motion-guard stop race — offline finding

> Date: 2026-11-26. Level: **L2 offline code probe**. No ROS, no gateway
> process, no publisher, no device and no hardware. The frozen
> `jcheng-manipulation` baseline `v1.0.0` was read, not modified.

## 1. Finding

The deployed `armpi_debug_gateway` uses separate guard calls:

```text
dispatch_motion:  validate() -> publish() -> start()
stop_motion:                          publish(hold) -> mark_stopped()
```

`MotionGuard` locks each method, not the sequence. A stop that arrives between
`validate()` and `start()` sees no active command, publishes a hold and returns
`idle`; the subsequent `start()` makes the command `executing` again. In the
probe the command then reaches `completed` after three target samples.

| Case | After stop | After start | After target samples | Stop lost |
|---|---|---|---|---|
| stop after start (control) | `stopped` | `stopped` | `stopped` | no |
| stop between validate and start | `idle` | `executing` | `completed` | **yes** |

This is the C2p "stop before publish" window that `jcheng_system_core` closed
in R2 by ordering publish and stop-hold under one session lock. The deployed
gateway is a different implementation and has not been patched.

## 2. Code references

| Fact | Location (frozen baseline) |
|---|---|
| `dispatch_motion` validates, publishes, then starts | `jcheng-manipulation/armpi-debug-studio/ros2_gateway/armpi_debug_gateway/node.py`, `dispatch_motion` |
| `stop_motion` publishes a hold then calls `mark_stopped` | same file, `stop_motion` |
| `MotionGuard` locks each method individually | `.../motion.py`, `_guarded` / `MotionGuard` |
| `validate` refuses only when `_active` is already set | `MotionGuard.validate` |
| `mark_stopped` with no active plan returns `idle` | `MotionGuard.mark_stopped` |

The exact lines move with the frozen baseline; the committed probe records the
source path and the observed state, not a vendored copy.

## 3. Reproduction

```bash
python3 tools/deployed_guard_race_probe.py \
  --output e2e/reports/20261126_deployed_guard_race/probe.json
```

The tool imports `armpi_debug_gateway.motion` from
`jcheng-manipulation/armpi-debug-studio/ros2_gateway` by default. It uses a
synthetic joint mapping, not a calibration, and prints
`RACE CONFIRMED` while exiting 0 so the evidence is captured. Raw output:
[`probe.json`](probe.json).

System tests and the repository gate:

```text
python3 -m pytest -q       pass (dots only; no failures)
scripts/check_repo_gate.sh OK
```

Raw output: [`pytest.txt`](pytest.txt), [`gate.txt`](gate.txt).

## 4. Impact

- A stop request can be acknowledged, followed by the command being published
  and reported `completed`. The stop is not merely unconfirmed; it is
  **lost** in the guard state.
- The M4/M5 motion target cannot rely on the deployed gateway's current
  dispatch/stop ordering. This must be fixed or the motion target must move to
  the already-fixed `jcheng_system_core` gateway.
- C1–C5 on the real deployed gateway still needs a separately authorized
  hardware session; this probe does not replace it.

## 5. Recommended fix

Choose one, then rerun the probe and the hardware C1–C5:

1. **One atomic dispatch:** `validate`, the stop-latch check, the command
   publish and the `start` transition under one guard/session lock, with the
   stop hold ordered inside the same lock. This is the R2 pattern in
   `jcheng_system_core`.
2. **Generation token:** `validate` returns a plan with a generation;
   `mark_stopped` increments the generation; `start` refuses a stale
   generation. The publisher must also check the generation before publishing.

The second form is safer if publishing can block; the first is simpler for the
current non-blocking vendor publisher.

## 6. Limitations

- Offline reproduction of the guard state machine, not the deployed HTTP
  process or its ROS callbacks.
- The synthetic mapping is not a robot calibration; the race is
  config-independent once validation succeeds.
- No physical motion and no physical-stop claim. No hardware was contacted.
- The frozen baseline is not edited; the fix belongs to the M4/M5 target or a
  separately authorized deployment change.
