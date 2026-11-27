"""ROS 2 process serving the hardware-backed HTTP gateway.

This is the M4/M5 target chosen on 2026-11-26: the system gateway with the
M5.2 ``jcheng_vendor`` executor, not the frozen ``armpi_debug_gateway``.

The node owns the only vendor ``VendorBridge``. It polls the bus at about
0.25 s to keep measured state fresh, serves ``GatewayApp`` over HTTP, and runs
the arbiter tick on the mock gateway's ``TickThread``. Motion must be enabled
explicitly; a missing token or ``enable_motion=false`` refuses to start.
"""

from __future__ import annotations

import os
from pathlib import Path

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import Bool

from jcheng_hardware.readonly import ReadOnlyMapping
from jcheng_system_core.config import RobotProfile, find_repo_root, load_contract
from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.gateway import GatewayApp, GatewayServer, TickThread
from jcheng_system_core.timebase import Clock

from .bridge import VendorBridge
from .gateway_stack import VendorStack


class VendorGatewayNode(Node):
    def __init__(self, *, parameter_overrides: list | None = None) -> None:
        super().__init__(
            "jcheng_gateway_vendor", parameter_overrides=parameter_overrides or []
        )
        self.declare_parameter("root", "")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 8766)
        self.declare_parameter("token", "")
        self.declare_parameter("enable_motion", False)
        self.declare_parameter("evidence_path", "")
        self.declare_parameter("bus_service", "/ros_robot_controller/bus_servo/get_state")
        self.declare_parameter("command_topic", "/servo_controller")
        self.declare_parameter("poll_s", 0.25)

        token = str(
            self.get_parameter("token").value
            or os.environ.get("JCHENG_GATEWAY_TOKEN", "")
        )
        if not bool(self.get_parameter("enable_motion").value):
            raise RuntimeError(
                "motion is disabled; start with enable_motion:=true only after "
                "the operator authorization and safety checks"
            )
        if not token:
            raise RuntimeError("a gateway token is required")

        root_value = str(
            self.get_parameter("root").value or os.environ.get("JCHENG_ROOT", "")
        )
        self.root = Path(root_value).expanduser() if root_value else find_repo_root()
        self.profile = RobotProfile.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
        )
        self.contract = load_contract(self.root / "configs" / "contract.yaml")
        self.mapping = ReadOnlyMapping.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
        )
        self.clock = Clock()
        evidence_path = str(self.get_parameter("evidence_path").value or "") or None
        self.bridge = VendorBridge(
            self,
            self.mapping,
            self.profile.profile_hash,
            self.clock,
            bus_service=str(self.get_parameter("bus_service").value),
            command_topic=str(self.get_parameter("command_topic").value),
        )
        self.stack = VendorStack.build(
            self.root,
            self.bridge,
            clock=self.clock,
            evidence_path=evidence_path,
        )
        self.app = GatewayApp(self.stack, token)
        self._host = str(self.get_parameter("host").value)
        self._port = int(self.get_parameter("port").value)
        self._poll_s = max(0.05, float(self.get_parameter("poll_s").value))
        self.server: GatewayServer | None = None
        self.ticker: TickThread | None = None

        callback_group = ReentrantCallbackGroup()
        self._callback_group = callback_group
        self.create_subscription(
            Bool,
            "/safety_stop",
            self._on_safety_stop,
            10,
            callback_group=callback_group,
        )
        # The node is not spinning yet in __init__, so a synchronous service
        # call here would deadlock. Do the first bus read from a timer after
        # the executor is spinning; only then start the HTTP server and tick.
        self._startup_timer = self.create_timer(
            0.5, self._startup, callback_group=callback_group
        )
        self.get_logger().info(
            "hardware gateway waiting for the first bus read; "
            "HTTP is not served until measured state is fresh"
        )

    def _startup(self) -> None:
        try:
            initial = self.stack.hardware.read_bus(timeout_s=2.0)
        except Exception as exc:  # noqa: BLE001 - startup boundary
            self.stack.safety.latch(
                ErrorCode.BUS_READ_FAILED, f"initial bus read failed: {exc}"
            )
            self.get_logger().error(f"initial bus read failed: {exc}; retrying")
            return
        self.stack.safety.update_state(initial.state)
        if self._startup_timer is not None:
            self.destroy_timer(self._startup_timer)
            self._startup_timer = None
        self.create_timer(
            self._poll_s, self._poll_state, callback_group=self._callback_group
        )
        self.server = GatewayServer(self.app, self._host, self._port)
        self.server.start()
        self.ticker = TickThread(self.stack)
        self.ticker.start()
        self.get_logger().info(
            "hardware gateway listening on "
            f"http://{self._host}:{self.server.port} "
            f"(mode=hardware, poll={self._poll_s:.2f}s)"
        )

    def _poll_state(self) -> None:
        try:
            sample = self.stack.hardware.read_bus(timeout_s=1.0)
            self.stack.safety.update_state(sample.state)
        except Exception as exc:  # noqa: BLE001 - safety boundary
            self.stack.safety.latch(
                ErrorCode.BUS_READ_FAILED, f"bus poll failed: {exc}"
            )

    def _on_safety_stop(self, message: Bool) -> None:
        if not bool(message.data):
            return
        try:
            self.stack.arbiter.stop("safety_supervisor", "safety stop input")
        except Exception as exc:  # noqa: BLE001 - safety boundary
            self.get_logger().error(f"stop input failed: {exc}")

    def destroy_node(self) -> None:
        if self._startup_timer is not None:
            self.destroy_timer(self._startup_timer)
            self._startup_timer = None
        if self.ticker is not None:
            self.ticker.stop()
        if self.server is not None:
            self.server.stop()
        super().destroy_node()


def main() -> int:
    rclpy.init(args=None)
    node = None
    executor = None
    try:
        node = VendorGatewayNode()
        executor = MultiThreadedExecutor(num_threads=6)
        executor.add_node(node)
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        if executor is not None:
            executor.shutdown()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
