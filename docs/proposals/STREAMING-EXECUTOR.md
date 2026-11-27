# Proposal — privileged multi-joint streaming executor (M6 blocker)

> Status: **Proposal only**, 2026-11-26. No code, IDL, schema, contract hash,
> lock file or hardware path is changed by this document. M6 hardware
> teleoperation remains blocked until this proposal is reviewed and implemented,
> or the M6 scope is reduced to single-joint steps.

## 1. Problem

`TeleopIntake` converts a 50 Hz `TeleopCommand` into a multi-joint `JOINT_STEP`
through `MotionArbiter`, but the only hardware executor
(`jcheng_vendor/executor.py`) accepts a **single-joint** `JOINT_STEP` at about
**5 commands/s**. A five-joint leader-follower loop needs the system layer to
apply a new multi-joint target at ≥10 Hz. Serializing five single-joint steps
per target yields ~1 Hz per joint and is not teleoperation; it is a sequence of
small moves with a stale target between steps.

Two further facts bound the design:

- `/joint_states` from the vendor stack is a **command cache**; completion and
  pose truth come from a bus read (`GetBusServoState`). Bus reads have no safe
  timeout, so a wedged reader needs the owning process restarted, not a retry
  loop.
- The platform has **no e-stop, brake, encoder or self-collision model**. A
  software stop is not an e-stop and must never be described as one.

## 2. Decision requested

Choose one:

1. **Implement a privileged multi-joint stream** on the system side, behind
   `MotionArbiter`, using a vendor capability that can accept a joint vector at
   ≥10 Hz. This is the M6 path; it needs a vendor capability review and a new
   privileged executor interface.
2. **Reduce M6 to supervised single-joint steps** at the vendor rate, with a
   leader arm feeding one joint at a time. This is honest but is not
   leader-follower teleoperation and should be named differently in ACCEPTANCE.
3. **Declare M6 hardware blocked on the vendor stack** and keep M6 at offline
   prerequisites until the vendor provides a stream.

Recommendation: the read-only probe (2026-11-27) found a vendor multi-servo
vector write, so pursue **option 1** with a mock-first bridge; rate and
interpolation still need an authorized write session. Do not emulate streaming
by a rapid series of single-joint steps; the intermediate poses are real robot
motion.

## 3. Required properties (any option)

| Property | Requirement |
|---|---|
| Authority | Only `MotionArbiter.execute` may call the stream; no capability, studio, teleop or tool calls it directly |
| Lease | Every streamed target carries a live priority-40 arm lease; loss aborts within one intake tick (≤100 ms at 10 Hz) and latches if the outcome is unknown |
| Deadman | A released/expired deadman aborts the target; no auto-resume after re-press |
| Limits | Each target is validated against profile position/velocity limits and the maximum per-tick delta; the stream never bypasses `SafetySupervisor` |
| Stop | `MotionArbiter.stop` aborts the active target and latches `SAFETY_STOP_LATCHED`; the stop result must say `confirmed`, `unconfirmed` or `unknown` (see `jcheng-interfaces/docs/proposals/stop-confirmation-levels.md`), never a bare `accepted` |
| Truth | Completion and pose truth come from a bus read; a command cache is labelled `commanded`, never `measured` |
| Failure | A bus read failure, unknown vendor response or publisher-guard violation latches; a wedged reader means restart the owning process |
| Evidence | The arbiter's evidence store records every streamed target, abort, stop and verification outcome |
| Scope | ArmPi bus-servo first; PiPER X is a separate CAN bridge and profile v2 proposal, not a variant of this executor |

## 4. Proposed interface (privileged, not a contract yet)

A narrow port beside the existing bridge, owned by `jcheng_system_core`:

```text
class StreamingBridge(Protocol):
    def stream(self, command: MotionCommand) -> StreamAck: ...
    def hold(self, now: float) -> None: ...
    def bus_read(self, now: float) -> RobotState: ...
```

- `MotionCommand.kind = "JOINT_STREAM"` (system-internal, not IDL): joint names,
  target positions, source sequence and timestamp, lease ref hash.
- `StreamAck` = `accepted | rejected(code) | unknown`; `unknown` latches.
- The bridge is the **only** object that talks to the vendor; it stays behind
  the arbiter, publisher guard and safety supervisor.
- A new error code for "stream unsupported by the vendor" is a system error
  registry change, not a new wire message.

No `TeleopCommand` change is implied here; the wire v0.2 proposal
(`jcheng-teleop/docs/proposals/TELEOP_COMMAND_V0.2.md`) supplies the position
payload that the intake would map into `JOINT_STREAM`.

## 5. Read-only capability probe (first task, no motion)

**Result (2026-11-27): a multi-servo vector write exists in the vendor stack.**
The probe read files inside the running `ArmPiUltra` container only; no ROS
command, no `/dev/rrc` write, no servo enable and no motion.

- The vendor node subscribes `~/bus_servo/set_position` (`ServosPosition`).
- `ServosPosition` = `float64 duration` + `ServoPosition[]` (`uint16 id`,
  `uint16 position` pulses).
- `bus_servo_set_position(duration, positions)` packs every id/position into one
  packet (`duration` in ms) over `/dev/rrc` at 1 Mbps.
- No completion acknowledgement; truth still comes from `GetBusServoState`.
- The **JCHENG** executor's single-joint limit is in `jcheng_vendor/executor.py`
  (`send_step` per joint + settle), not in the vendor board.

This makes option 1 plausible without new firmware. It is **not proven** until
an authorized write session measures the sustainable rate and whether the servo
interpolates between duration targets. The probe itself is read-only.

Evidence:
[`e2e/reports/20261127_streaming_probe/`](../../e2e/reports/20261127_streaming_probe/README.md).

Next step: a mock-first `StreamingBridge` design that packs all joints into one
`ServosPosition` call with a duration matching the command period, verifies via
a bus read, and stays behind the arbiter. Then, only with separate per-session
authorization, a supervised low-speed write session measures rate/tracking.

The probe requires no writes; any subsequent write session requires separate
operator authorization.

## 6. Test plan (mock first)

- Mock streaming bridge at 10–20 Hz: multi-joint targets reach it in order, the
  per-tick delta is capped, and no target is emitted without a lease/deadman.
- Lease revoke/expiry mid-stream aborts within one tick and latches when the
  outcome is unknown.
- Deadman release and input timeout abort within one tick; stop latches.
- Publisher guard: a second publisher or a missing lease is rejected before the
  bridge.
- Bus-read failure latches; a stale `/joint_states` never counts as completion.
- Humble container build and tests; no hardware.
- A separate, authorized low-speed hardware session follows, with numeric
  latency/tracking bounds declared before the run.

## 7. Acceptance for this proposal

- The probe result and the chosen option are recorded with evidence.
- If option 1: the mock stream tests above pass, the contract proposal for the
  teleop wire is approved, and a supervised hardware plan exists.
- If option 3: M6 is documented as blocked on the vendor stack and ACCEPTANCE
  says so; no single-joint emulation is reported as leader-follower teleop.

## 8. Non-goals

E-stop claims, force feedback, Cartesian motion, self-collision avoidance,
unattended motion, PiPER X CAN streaming, industrial certification.
