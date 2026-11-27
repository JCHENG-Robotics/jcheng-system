"""ROS-graph integration: R10 HTTP gateway over a fake vendor controller.

No hardware: a fake node serves GetBusServoState and consumes
/servo_controller, so the real VendorBridge/HardwareExecutor/GatewayApp path is
exercised under Humble. Skipped where the vendor message packages are absent.
"""

from __future__ import annotations

import json
import time
import urllib.request

import pytest

TOKEN = "integration-token"


def _http(port: int, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def test_gateway_over_fake_vendor_graph() -> None:
    pytest.importorskip("servo_controller_msgs")
    pytest.importorskip("ros_robot_controller_msgs")
    import rclpy
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from rclpy.parameter import Parameter
    from ros_robot_controller_msgs.msg import GetBusServoCmd, BusServoState
    from ros_robot_controller_msgs.srv import GetBusServoState
    from servo_controller_msgs.msg import ServosPosition

    from jcheng_vendor.gateway_node import VendorGatewayNode

    class FakeVendor(Node):
        def __init__(self) -> None:
            super().__init__("fake_vendor_controller")
            self.pulses = {1: 700, 2: 500, 3: 500, 4: 500, 5: 500, 6: 500}
            self.commands = 0
            group = ReentrantCallbackGroup()
            self.create_subscription(
                ServosPosition, "/servo_controller", self._on_command, 10,
                callback_group=group,
            )
            self.create_service(
                GetBusServoState,
                "/ros_robot_controller/bus_servo/get_state",
                self._on_bus,
                callback_group=group,
            )

        def _on_command(self, message: ServosPosition) -> None:
            for item in message.position:
                self.pulses[int(item.id)] = int(item.position)
            self.commands += 1

        def _on_bus(self, request, response):
            response.success = True
            for command in request.cmd:
                state = BusServoState()
                state.present_id = [int(command.id)]
                state.position = [self.pulses.get(int(command.id), 500)]
                response.state.append(state)
            return response

    rclpy.init()
    fake = None
    gateway = None
    executor = None
    try:
        fake = FakeVendor()
        gateway = VendorGatewayNode(
            parameter_overrides=[
                Parameter("root", value=str(__file__).rsplit("/tests_ros/", 1)[0]),
                Parameter("token", value=TOKEN),
                Parameter("enable_motion", value=True),
                Parameter("host", value="127.0.0.1"),
                Parameter("port", value=0),
                Parameter("poll_s", value=0.1),
                Parameter("evidence_path", value="/tmp/jcheng-integration.jsonl"),
            ]
        )
        executor = MultiThreadedExecutor(num_threads=6)
        executor.add_node(fake)
        executor.add_node(gateway)
        import threading

        thread = threading.Thread(target=executor.spin, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10.0
        while gateway.server is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert gateway.server is not None, "gateway did not start after bus read"
        port = gateway.server.port

        health = _http(port, "GET", "/v1/health")
        assert health["capabilities"] == ["hardware"]

        contract = gateway.stack.version.to_dict()
        armed = _http(
            port,
            "POST",
            "/v1/safety/arm",
            {
                "operator_id": "operator",
                "checklist_hash": "integration",
                "confirm_phrase": gateway.stack.safety.limits.arm_confirm_phrase,
                "contract": contract,
                "profile_hash": gateway.profile.profile_hash,
            },
        )
        assert armed["armed"] is True

        lease = _http(
            port,
            "POST",
            "/v1/leases",
            {
                "holder": "operator",
                "scope": "arm",
                "mode": "exclusive",
                "priority": 100,
                "ttl_ms": 30_000,
                "deadman_required": False,
                "contract": contract,
            },
        )
        lease_id = lease["lease_id"]

        for _ in range(100):
            if gateway.bridge._publisher.get_subscription_count() >= 1:
                break
            time.sleep(0.05)
        command = gateway.stack.command(lease_id=lease_id, duration_s=0.5)
        result = _http(port, "POST", "/v1/motion/execute", command.to_dict())
        assert result["outcome"] == "COMPLETED", result
        assert result["bus_verified"] is True
        assert fake.commands >= 1

        stop = _http(
            port,
            "POST",
            "/v1/motion/stop",
            {"operator_id": "operator", "reason": "integration"},
        )
        assert stop["accepted"] is True
    finally:
        if executor is not None:
            executor.shutdown()
        if gateway is not None:
            gateway.destroy_node()
        if fake is not None:
            fake.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
