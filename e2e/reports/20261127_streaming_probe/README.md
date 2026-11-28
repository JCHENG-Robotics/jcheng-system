# Vendor streaming capability probe (read-only)

> Status: **Read-only, 2026-11-27**. No servo write, no enable, no motion and no
> container restart. The probe inspected files inside the running
> `ArmPiUltra` container with `docker exec ... cat/grep/find` only.

## Access note

`pi@10.42.0.218` only accepts `~/.ssh/id_ed25519_armpi`; the agent's
`armpi-clock-sync` key is a forced-command key (`usage: get | set <epoch>`) and
must not be used for shell access. The probe used `IdentitiesOnly=yes`.

## Findings

1. **The vendor node subscribes a multi-servo position topic.**
   `ros_robot_controller_node.py`:
   `create_subscription(ServosPosition, '~/bus_servo/set_position', ...)`.
2. **The SDK writes all listed servos in one serial packet with a duration.**
   `ros_robot_controller_sdk.py:514`:

   ```python
   def bus_servo_set_position(self, duration, positions):
       duration = int(duration * 1000)
       data = [0x01, duration & 0xFF, 0xFF & (duration >> 8), len(positions)]
       for i in positions:
           data.extend(struct.pack("<BH", i[0], i[1]))
       self.buf_write(PacketFunction.PACKET_FUNC_BUS_SERVO, data)
   ```

   Serial port `/dev/rrc`, baud 1,000,000, timeout 5 s.
3. **Message shape** (`ros_robot_controller_msgs`):
   `ServosPosition.msg` = `float64 duration` + `ServoPosition[] position`;
   `ServoPosition.msg` = `uint16 id` + `uint16 position` (pulses, not radians).
4. **No completion acknowledgement.** The packet is fire-and-forget; truth and
   completion still come from `GetBusServoState` (`~/bus_servo/get_state`).
5. **The JCHENG executor's single-joint limitation is in JCHENG, not the
   vendor board.** `jcheng_vendor/executor.py` calls `send_step` per joint,
   serializes one `JOINT_STEP` at a time and waits for a settle deadline. The
   vendor SDK already accepts a vector.

## Consequence for the M6 proposal

[`docs/proposals/STREAMING-EXECUTOR.md`](../../docs/proposals/STREAMING-EXECUTOR.md)
option 1 (privileged multi-joint stream) is **plausible without new firmware**:
a system-side bridge can publish `ServosPosition` at a bounded rate with a
duration that matches the command period, then verify with a bus read.

It is not proven yet:

- the maximum sustainable `bus_servo_set_position` rate over the 1 Mbps link is
  unmeasured;
- whether the servo interpolates smoothly between duration targets or snaps is
  unmeasured;
- a write session moves the arm and needs separate, explicit, per-session
  operator authorization; this probe did not authorize it.

The next step is a **mock-first** design: a `StreamingBridge` behind
`MotionArbiter` with a mode that packs all joints into one `ServosPosition`
call, plus bus-read completion. Only then a supervised, authorized low-speed
session may measure rate and tracking.

## Not done

- No ROS command inside the container (no `ros2 topic pub`, no service call).
- No `/dev/rrc` write, no servo enable, no motion, no container restart.
- No rate or tracking numbers; those require the authorized write session.
