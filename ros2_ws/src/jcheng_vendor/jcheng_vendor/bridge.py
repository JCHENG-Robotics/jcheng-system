"""ROS vendor bridge for M5.2.

The bridge is the only publisher of ``/servo_controller``. It sends exactly
one step or gripper target at a time, reads positions only through
``GetBusServoState``, and can hold the last measured pulses on a stop.
"""

from __future__ import annotations

from collections.abc import Mapping
import threading
import time
from typing import Any

from rclpy.node import Node
from ros_robot_controller_msgs.msg import GetBusServoCmd
from ros_robot_controller_msgs.srv import GetBusServoState
from servo_controller_msgs.msg import ServoPosition, ServosPosition

from jcheng_hardware.readonly import ReadOnlyMapping, validate_pulse
from jcheng_system_core.models import RobotState
from jcheng_system_core.timebase import Clock

from .executor import BusSample


class VendorBridge:
    publishers = {"jcheng_hardware"}

    def __init__(
        self,
        node: Node,
        mapping: ReadOnlyMapping,
        profile_hash: str,
        clock: Clock,
        bus_service: str = "/ros_robot_controller/bus_servo/get_state",
        command_topic: str = "/servo_controller",
    ) -> None:
        self.node = node
        self.mapping = mapping
        self.profile_hash = profile_hash
        self.clock = clock
        self._publisher = node.create_publisher(ServosPosition, command_topic, 10)
        self._client = node.create_client(GetBusServoState, bus_service)
        self._read_lock = threading.Lock()
        self._publish_lock = threading.Lock()
        self._pulses: dict[str, int] = {
            joint.name: int(joint.init_raw) for joint in mapping.joints
        }
        self._last_at: float | None = None
        self._last_error: str = ""
        self.sent: list[dict[str, Any]] = []

    # ---- bus read ----

    def read_bus(self, timeout_s: float = 1.0) -> BusSample:
        with self._read_lock:
            if not self._client.service_is_ready():
                if not self._client.wait_for_service(timeout_sec=min(float(timeout_s), 1.0)):
                    raise RuntimeError("GetBusServoState service is unavailable")
            request = GetBusServoState.Request()
            for servo_id in self.mapping.read_order:
                command = GetBusServoCmd()
                command.id = int(servo_id)
                command.get_position = 1
                request.cmd.append(command)
            event = threading.Event()
            box: dict[str, Any] = {}

            def done(future: Any) -> None:
                try:
                    box["result"] = future.result()
                except Exception as exc:  # noqa: BLE001 - ROS callback boundary
                    box["error"] = str(exc)
                event.set()

            future = self._client.call_async(request)
            future.add_done_callback(done)
            if not event.wait(max(0.1, float(timeout_s))):
                raise RuntimeError("GetBusServoState timed out")
            if "error" in box:
                raise RuntimeError(box["error"])
            response = box.get("result")
            if response is None or not response.success:
                raise RuntimeError("GetBusServoState was not successful")
            states = list(response.state)
            if len(states) != len(self.mapping.read_order):
                raise RuntimeError(
                    f"bus response count {len(states)} != {len(self.mapping.read_order)}"
                )
            pulses: dict[str, int] = {}
            for servo_id, state in zip(self.mapping.read_order, states):
                positions = list(state.position)
                if not positions:
                    raise RuntimeError(f"servo {servo_id} returned no position")
                pulse = validate_pulse(int(positions[0]))
                joint = self.mapping.joint_by_servo(servo_id)
                pulses[joint.name] = pulse
            with self._publish_lock:
                self._pulses.update(pulses)
                self._last_at = self.clock.now()
                self._last_error = ""
            return BusSample(state=self.state(), pulses=dict(pulses))

    def state(self) -> RobotState:
        now = self.clock.now()
        fresh = self._last_at is not None and (now - self._last_at) <= 0.5
        arm_joints = sorted(self.mapping.arm_joints, key=lambda joint: joint.name)
        positions = [
            (self._pulses[joint.name] - joint.init_raw)
            * joint.direction
            * self.mapping.radians_per_tick
            for joint in arm_joints
        ]
        gripper = self.mapping.gripper_joint
        gripper_position = (
            (self._pulses[gripper.name] - gripper.init_raw)
            * gripper.direction
            * self.mapping.radians_per_tick
        )
        return RobotState(
            joint_names=[joint.name for joint in arm_joints],
            positions=positions,
            velocities=[0.0 for _ in positions],
            gripper_position=gripper_position,
            base_duty=[0.0, 0.0, 0.0, 0.0],
            battery_voltage=0.0,
            profile_hash=self.profile_hash,
            truth="measured" if self._last_at is not None else "commanded",
            verification="bus" if self._last_at is not None else "timed",
            fresh=fresh,
            stamp=self._last_at if self._last_at is not None else now,
        )

    # ---- command path ----

    def _pulse_for(self, joint_name: str, radians: float) -> int:
        joint = next((item for item in self.mapping.joints if item.name == joint_name), None)
        if joint is None:
            raise ValueError(f"unknown joint {joint_name}")
        pulse = int(round(joint.init_raw + joint.direction * float(radians) / self.mapping.radians_per_tick))
        return validate_pulse(pulse)

    def _publish(self, entries: list[tuple[int, int]], duration_s: float) -> None:
        message = ServosPosition()
        message.duration = max(0.02, min(30.0, float(duration_s)))
        message.position_unit = "pulse"
        for servo_id, pulse in entries:
            position = ServoPosition()
            position.id = int(servo_id)
            position.position = float(validate_pulse(int(pulse)))
            message.position.append(position)
        with self._publish_lock:
            self._publisher.publish(message)
            self.sent.append(
                {
                    "at": self.clock.now(),
                    "duration_s": message.duration,
                    "entries": [[int(servo_id), int(pulse)] for servo_id, pulse in entries],
                }
            )

    def send_step(self, joint_name: str, target_rad: float, duration_s: float) -> int:
        joint = next((item for item in self.mapping.arm_joints if item.name == joint_name), None)
        if joint is None:
            raise ValueError(f"{joint_name} is not an arm joint")
        pulse = self._pulse_for(joint_name, target_rad)
        self._publish([(joint.servo_id, pulse)], duration_s)
        return pulse

    def send_gripper(self, target_rad: float, duration_s: float) -> int:
        joint = self.mapping.gripper_joint
        pulse = self._pulse_for(joint.name, target_rad)
        self._publish([(joint.servo_id, pulse)], duration_s)
        return pulse

    def hold(self) -> None:
        """Hold all joints at the latest measured pulses."""
        entries = [
            (joint.servo_id, self._pulses[joint.name])
            for joint in self.mapping.joints
        ]
        self._publish(entries, 0.02)

    def health(self) -> dict[str, Any]:
        return {
            "mode": "vendor-readonly-command",
            "last_bus_at": self._last_at,
            "last_error": self._last_error,
            "publisher": "/servo_controller",
            "sent": len(self.sent),
        }
