"""ROS 2 read-only telemetry node.

The node subscribes to vendor state topics and calls only the read-only
``GetBusServoState`` service. It publishes only ``RobotState`` and
``SafetyState`` on ``/jcheng/telemetry/*``. It has no vendor command
publisher, no write service client, and no serial device access.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
from typing import Any

from builtin_interfaces.msg import Time
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, JointState
from std_msgs.msg import UInt16
from tf2_msgs.msg import TFMessage

from jcheng_interfaces.msg import RobotState, SafetyState
from jcheng_system_core.config import RobotProfile, find_repo_root, load_contract
from jcheng_system_core.timebase import Clock

from jcheng_hardware.readonly import ReadOnlyBridge, ReadOnlyMapping, ReadOnlyViolation

from .core import TelemetryCache
from .evidence import ReadOnlyEvidence, write_summary
from .gateway import ReadOnlyGatewayApp, ReadOnlyGatewayServer
from .preflight import (
    DomainConfig,
    PreflightInput,
    ReadOnlyConfig,
    load_lock_expectations,
    run_preflight,
)

try:  # Vendor workspace is present on the robot; CI only builds the package.
    from ros_robot_controller_msgs.msg import GetBusServoCmd
    from ros_robot_controller_msgs.srv import GetBusServoState
except ImportError:  # pragma: no cover - exercised on the robot, not CI
    GetBusServoCmd = None
    GetBusServoState = None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class TelemetryNode(Node):
    def __init__(self) -> None:
        super().__init__("jcheng_telemetry")
        self.declare_parameter("root", "")
        self.declare_parameter("domain_id", 0)
        self.declare_parameter("bus_service", "/ros_robot_controller/bus_servo/get_state")
        self.declare_parameter("bus_rate_hz", 1.0)
        self.declare_parameter("publish_rate_hz", 10.0)
        self.declare_parameter("require_bus", True)
        self.declare_parameter("gateway_port", 0)
        self.declare_parameter("gateway_token", "")
        self.declare_parameter("gateway_host", "127.0.0.1")
        self.declare_parameter("system_version", "0.2.0-dev")
        self.declare_parameter("evidence_path", "")
        self.declare_parameter("evidence_rate_hz", 1.0)

        root_value = str(self.get_parameter("root").value or os.environ.get("JCHENG_ROOT", ""))
        self.root = Path(root_value).expanduser() if root_value else find_repo_root()
        self.profile: RobotProfile = RobotProfile.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
        )
        self.contract = load_contract(self.root / "configs" / "contract.yaml")
        self.mapping = ReadOnlyMapping.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
        )
        self.domains = DomainConfig.from_file(self.root / "configs" / "domains.yaml")
        self.readonly = ReadOnlyConfig.from_file(self.root / "configs" / "readonly.yaml")
        self.clock = Clock()
        self.cache = TelemetryCache(
            self.profile.joint_names,
            self.profile.profile_hash,
            self.clock,
            state_timeout_s=0.5,
            battery_timeout_s=5.0,
        )
        evidence_path = str(self.get_parameter("evidence_path").value or "")
        self.evidence = (
            ReadOnlyEvidence(self.clock, evidence_path) if evidence_path else None
        )

        self._seq = 0
        self._clock_offset_ms = 0.0
        self._bus_lock = threading.RLock()
        self._pending_pulses: dict[int, int] | None = None
        self._bus_future = None
        self._bus_client = None
        self._bridge = ReadOnlyBridge(self.mapping, self._take_pending_pulses)
        self._gateway: ReadOnlyGatewayServer | None = None
        self._preflight_done = False

        self._state_pub = self.create_publisher(
            RobotState, "/jcheng/telemetry/state", 10
        )
        self._safety_pub = self.create_publisher(
            SafetyState, "/jcheng/telemetry/safety", 10
        )

        self.create_subscription(
            JointState,
            "/controller_manager/joint_states",
            self._on_joint_state,
            10,
        )
        self.create_subscription(
            UInt16,
            "/ros_robot_controller/battery",
            self._on_battery,
            10,
        )
        self.create_subscription(
            Image,
            "/depth_cam/rgb/image_raw",
            lambda _msg: self.cache.update_topic("/depth_cam/rgb/image_raw"),
            qos_profile_sensor_data,
        )
        self.create_subscription(
            TFMessage,
            "/tf",
            lambda _msg: self.cache.update_topic("/tf"),
            10,
        )
        self.create_subscription(
            TFMessage,
            "/tf_static",
            lambda _msg: self.cache.update_topic("/tf_static"),
            10,
        )

        require_bus = _as_bool(self.get_parameter("require_bus").value)
        if GetBusServoState is None or GetBusServoCmd is None:
            if require_bus:
                raise RuntimeError(
                    "ros_robot_controller_msgs is unavailable; a read-only bus client is required"
                )
            self.get_logger().warning("vendor bus messages unavailable; measured state disabled")
        else:
            self._bus_client = self.create_client(
                GetBusServoState, str(self.get_parameter("bus_service").value)
            )

        rate = max(0.1, float(self.get_parameter("bus_rate_hz").value))
        self.create_timer(1.0 / rate, self._request_bus_read)
        publish_rate = max(1.0, float(self.get_parameter("publish_rate_hz").value))
        self.create_timer(1.0 / publish_rate, self._publish)
        evidence_rate = max(0.1, float(self.get_parameter("evidence_rate_hz").value))
        self.create_timer(1.0 / evidence_rate, self._record_evidence)
        self.create_timer(2.0, self._run_preflight_once)
        self._start_gateway()
        self.get_logger().info(
            "read-only telemetry started: domain=%s mapping=%s writes=0"
            % (os.environ.get("ROS_DOMAIN_ID", "0"), self.mapping.mapping_hash)
        )

    # ---- bus read path (read-only) ----

    def _take_pending_pulses(self, _ids: list[int]) -> dict[int, int]:
        with self._bus_lock:
            if self._pending_pulses is None:
                raise ReadOnlyViolation("no pending bus sample")
            pulses = self._pending_pulses
            self._pending_pulses = None
            return pulses

    def _request_bus_read(self) -> None:
        if self._bus_client is None or self._bus_future is not None:
            return
        if not self._bus_client.service_is_ready():
            self.get_logger().warning("read-only bus service is not ready", throttle_duration_sec=5.0)
            return
        request = GetBusServoState.Request()
        for servo_id in self.mapping.read_order:
            command = GetBusServoCmd()
            command.id = int(servo_id)
            command.get_position = 1
            request.cmd.append(command)
        self._bus_future = self._bus_client.call_async(request)
        self._bus_future.add_done_callback(self._on_bus_response)

    def _on_bus_response(self, future: Any) -> None:
        self._bus_future = None
        try:
            response = future.result()
        except Exception as exc:  # noqa: BLE001 - ROS future boundary
            self.get_logger().error(f"read-only bus call failed: {exc}")
            return
        if response is None or not response.success:
            self.get_logger().error("read-only bus call was not successful")
            return
        states = list(response.state)
        if len(states) != len(self.mapping.read_order):
            self.get_logger().error(
                f"bus response count {len(states)} != {len(self.mapping.read_order)}"
            )
            return
        pulses: dict[int, int] = {}
        for servo_id, state in zip(self.mapping.read_order, states):
            positions = list(state.position)
            if not positions:
                self.get_logger().error(f"bus response for servo {servo_id} has no position")
                return
            pulses[int(servo_id)] = int(positions[0])
        try:
            with self._bus_lock:
                self._pending_pulses = pulses
                sample = self._bridge.read(self.clock.now())
                self.cache.update_measured(sample)
        except (ReadOnlyViolation, ValueError) as exc:
            self.get_logger().error(f"read-only sample rejected: {exc}")

    # ---- subscriptions ----

    def _on_joint_state(self, msg: JointState) -> None:
        stamp = float(msg.header.stamp.sec) + float(msg.header.stamp.nanosec) * 1e-9
        if stamp > 0.0:
            now = float(self.get_clock().now().nanoseconds) * 1e-9
            self._clock_offset_ms = (now - stamp) * 1000.0
        try:
            self.cache.update_joint_state(list(msg.name), list(msg.position))
        except ValueError as exc:
            self.get_logger().warning(f"joint state rejected: {exc}")

    def _on_battery(self, msg: UInt16) -> None:
        try:
            self.cache.update_battery_mv(int(msg.data))
        except ValueError as exc:
            self.get_logger().warning(f"battery value rejected: {exc}")

    # ---- publishers ----

    def _publish(self) -> None:
        now = self.clock.now()
        snapshot = self.cache.snapshot(now)
        self._seq += 1
        state = RobotState()
        state.header.stamp = self.get_clock().now().to_msg()
        state.header.frame_id = "base_link"
        state.seq = self._seq
        state.source = "jcheng_hardware"
        state.fresh = snapshot.fresh
        state.joint_names = list(snapshot.joint_names)
        state.positions = [float(value) for value in snapshot.positions]
        state.velocities = [float(value) for value in snapshot.velocities]
        state.gripper_position = float(snapshot.gripper_position)
        state.base_duty = [float(value) for value in snapshot.base_duty]
        state.battery_voltage = float(snapshot.battery_voltage)
        state.profile_hash = snapshot.profile_hash
        state.truth = snapshot.truth
        state.verification = snapshot.verification
        self._state_pub.publish(state)

        view = self.cache.safety_view(self.profile.config_hash, now)
        safety = SafetyState()
        safety.header.stamp = self.get_clock().now().to_msg()
        safety.header.frame_id = "base_link"
        safety.seq = view.seq
        safety.state = view.state
        safety.stop_latched = view.stop_latched
        safety.violation_codes = list(view.violation_codes)
        safety.violations = list(view.violations)
        safety.config_hash = view.config_hash
        safety.profile_hash = view.profile_hash
        safety.last_valid_state_at = self._stamp_msg(view.last_valid_state_at)
        self._safety_pub.publish(safety)

    def _stamp_msg(self, seconds: float) -> Time:
        wall = self.clock.t0_wall.timestamp() + float(seconds)
        sec = int(wall)
        nanosec = int((wall - sec) * 1e9)
        if nanosec >= 1_000_000_000:
            sec += 1
            nanosec -= 1_000_000_000
        return Time(sec=sec, nanosec=nanosec)

    def _record_evidence(self) -> None:
        if self.evidence is None:
            return
        now = self.clock.now()
        self.evidence.record_sample(self.cache.snapshot(now), self.cache.health(now))

    # ---- preflight and gateway ----

    def _run_preflight_once(self) -> None:
        if self._preflight_done:
            return
        self._preflight_done = True
        now = self.clock.now()
        health = self.cache.health(now)
        try:
            expected_contract, expected_profile = load_lock_expectations(self.root)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            self.get_logger().error(f"cannot read lockfile expectations: {exc}")
            return
        data = PreflightInput(
            role="vendor",
            domain_id=int(os.environ.get("ROS_DOMAIN_ID", "0")),
            contract_hash=self.contract.version.contract_hash,
            expected_contract_hash=expected_contract,
            profile_hash=self.profile.profile_hash,
            expected_profile_hash=expected_profile,
            clock_offset_ms=self._clock_offset_ms,
            joint_state_age_s=health["commanded_age_s"],
            bus_age_s=health["measured_age_s"],
            battery_age_s=health["battery_age_s"],
            write_count=0,
            max_clock_offset_ms=50.0,
            max_state_age_s=0.5,
            max_battery_age_s=5.0,
            require_bus=_as_bool(self.get_parameter("require_bus").value),
        )
        report = run_preflight(self.domains, self.readonly, data)
        if report.ok:
            self.get_logger().info("read-only preflight OK")
        else:
            self.get_logger().warning(f"read-only preflight degraded: {report.errors}")

    def _start_gateway(self) -> None:
        port = int(self.get_parameter("gateway_port").value)
        token = str(self.get_parameter("gateway_token").value)
        if port <= 0:
            return
        if not token:
            self.get_logger().warning("gateway_port set but gateway_token is empty; gateway disabled")
            return
        app = ReadOnlyGatewayApp(
            cache=self.cache,
            contract=self.contract.version.to_dict(),
            profile_hash=self.profile.profile_hash,
            config_hash=self.profile.config_hash,
            domain_id=int(os.environ.get("ROS_DOMAIN_ID", "0")),
            evidence=self.evidence or ReadOnlyEvidence(self.clock),
            token=token,
            system_version=str(self.get_parameter("system_version").value),
        )
        self._gateway = ReadOnlyGatewayServer(
            app,
            host=str(self.get_parameter("gateway_host").value),
            port=port,
        )
        self._gateway.start()
        self.get_logger().info(f"read-only gateway listening on http://{app_host(self._gateway)}")

    def shutdown(self) -> None:
        if self._gateway is not None:
            self._gateway.stop()
            self._gateway = None
        if self.evidence is not None:
            path = str(self.get_parameter("evidence_path").value or "")
            if path:
                summary = self.evidence.summary(
                    require_bus=_as_bool(self.get_parameter("require_bus").value),
                    max_gap_s=2.0,
                )
                summary["mode"] = "readonly"
                summary["writes"] = 0
                write_summary(Path(path).with_suffix(".summary.json"), summary)
        if self._bus_client is not None:
            self.destroy_client(self._bus_client)
            self._bus_client = None


def app_host(server: ReadOnlyGatewayServer) -> str:
    return f"127.0.0.1:{server.port}"


def main() -> int:
    rclpy.init(args=None)
    node = None
    try:
        node = TelemetryNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.shutdown()
            node.destroy_node()
        rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
