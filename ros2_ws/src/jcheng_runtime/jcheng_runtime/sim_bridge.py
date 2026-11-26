"""Test-only bridge from the system mock hardware port to the simulation plant.

The bridge publishes multi-joint position setpoints to `/sim/joint_command` and
reads `/sim/joint_states`. It is intended for domain 43 L3 tests only; it does
not join domain 41/42 and does not make the plant a motion authority. The
system arbiter remains the only caller of this bridge.
"""

from __future__ import annotations

import math

from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import JointState

from jcheng_system_core.errors import BusReadFailed
from jcheng_system_core.models import RobotState


class RosSimHardwareBridge:
    def __init__(self, node: Node, profile, clock) -> None:
        self.node = node
        self.profile = profile
        self.clock = clock
        self.positions = [0.0] * len(profile.joint_names)
        self.velocities = [0.0] * len(profile.joint_names)
        self.gripper_position = 0.0
        self.base_duty = [0.0, 0.0, 0.0, 0.0]
        self.battery_voltage = 0.0
        self.truth = "measured"
        self.verification = "simulated"
        # The sim plant starts at the profile's zero pose. Seed a fresh state
        # so the runtime's first safety tick does not latch STATE_STALE before
        # the first /sim/joint_states message arrives.
        self.fresh = True
        self.stamp = float(clock.now())
        self.publishers = {"jcheng_hardware"}
        self.bus_failed = False
        qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self._command_pub = node.create_publisher(JointState, "/sim/joint_command", qos)
        self._state_sub = node.create_subscription(
            JointState, "/sim/joint_states", self._on_state, qos
        )

    def add_publisher(self, name: str) -> None:
        self.publishers.add(name)

    def remove_publisher(self, name: str) -> None:
        self.publishers.discard(name)

    def set_bus_failure(self, failed: bool = True) -> None:
        self.bus_failed = failed

    def _on_state(self, msg: JointState) -> None:
        index = {name: position for position, name in enumerate(self.profile.joint_names)}
        for name, position in zip(msg.name, msg.position):
            if name in index and math.isfinite(float(position)):
                self.positions[index[name]] = float(position)
        for name, velocity in zip(msg.name, msg.velocity):
            if name in index and math.isfinite(float(velocity)):
                self.velocities[index[name]] = float(velocity)
        self.stamp = self.clock.now()
        self.fresh = True

    def state(self, now: float | None = None) -> RobotState:
        now = self.clock.now() if now is None else now
        age = now - self.stamp if self.stamp else float("inf")
        return RobotState(
            joint_names=list(self.profile.joint_names),
            positions=list(self.positions),
            velocities=list(self.velocities),
            gripper_position=float(self.gripper_position),
            base_duty=list(self.base_duty),
            battery_voltage=float(self.battery_voltage),
            profile_hash=self.profile.profile_hash,
            truth=self.truth,
            verification=self.verification,
            fresh=bool(self.fresh and age <= 0.5),
            stamp=self.stamp,
        )

    def publish(self, command: MotionCommand, now: float | None = None) -> None:
        if self.bus_failed:
            raise BusReadFailed("sim bridge is unavailable")
        names: list[str] = []
        positions: list[float] = []
        if command.kind == "JOINT_TRAJECTORY" and command.trajectory is not None and command.trajectory.points:
            names = list(command.trajectory.joint_names)
            positions = list(command.trajectory.points[-1].positions)
        elif command.kind in {"JOINT_STEP", "HOME"}:
            names = list(command.joint_names or self.profile.joint_names)
            positions = list(command.target_positions or [0.0] * len(names))
        elif command.kind == "GRIPPER" and command.gripper is not None:
            names = ["r_joint"] if "r_joint" in self.profile.joint_names else []
            positions = [float(command.gripper.position_rad)]
        message = JointState()
        message.header.stamp = self.node.get_clock().now().to_msg()
        message.name = names
        message.position = positions
        self._command_pub.publish(message)

    def bus_read(self, now: float | None = None) -> RobotState:
        if self.bus_failed:
            raise BusReadFailed("sim bridge is unavailable")
        state = self.state(now)
        if not self.stamp:
            raise BusReadFailed("simulation state has not arrived")
        return state

    def hold(self, now: float | None = None) -> None:
        self.base_duty = [0.0, 0.0, 0.0, 0.0]

    def zero_base(self, now: float | None = None) -> None:
        self.base_duty = [0.0, 0.0, 0.0, 0.0]

    def tick(self, now: float | None = None) -> None:
        return None
