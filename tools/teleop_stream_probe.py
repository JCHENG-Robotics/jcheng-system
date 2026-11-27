"""L3 streaming probe: teleop -> streaming intake -> mock/sim bridge.

Publishes a multi-joint velocity script and reads the runtime's
`/jcheng/telemetry/state` on the same domain. The runtime must run with
`streaming:=true`; it opens one arbiter session, publishes 20 Hz frames and
closes on the neutral frame. Test-only; no hardware.
"""

from __future__ import annotations

import argparse
import json
import math
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from jcheng_interfaces.msg import RobotState, TeleopCommand
from jcheng_interfaces.srv import AcquireLease, ArmSystem, ReleaseLease

CONTRACT = "sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42"


def _velocity(raw: str, joints: int) -> list[float]:
    values = [float(item) for item in raw.split(",") if item.strip()]
    if len(values) != joints:
        raise SystemExit(f"--velocity needs {joints} values, got {len(values)}")
    if not all(math.isfinite(value) for value in values):
        raise SystemExit("--velocity must be finite")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joints", type=int, required=True)
    parser.add_argument("--velocity", default="")
    parser.add_argument("--duration-s", type=float, default=1.2)
    parser.add_argument("--rate-hz", type=float, default=50.0)
    parser.add_argument("--state-topic", default="/jcheng/telemetry/state")
    args = parser.parse_args(argv)
    if args.joints <= 0 or args.duration_s <= 0 or args.rate_hz <= 0:
        raise SystemExit("joints, duration and rate must be positive")
    velocities = _velocity(
        args.velocity or ",".join(["0.3"] * args.joints), args.joints
    )

    rclpy.init()
    node = Node("teleop_stream_probe")
    latest: dict[str, list[float] | None] = {"first": None, "last": None}

    def on_state(msg: RobotState) -> None:
        if latest["first"] is None:
            latest["first"] = list(msg.positions)
        latest["last"] = list(msg.positions)

    qos = QoSProfile(
        depth=1,
        history=HistoryPolicy.KEEP_LAST,
        reliability=ReliabilityPolicy.BEST_EFFORT,
    )
    node.create_subscription(RobotState, args.state_topic, on_state, qos)
    arm_client = node.create_client(ArmSystem, "/jcheng/safety/arm")
    lease_client = node.create_client(AcquireLease, "/jcheng/authority/acquire_lease")
    for client in (arm_client, lease_client):
        if not client.wait_for_service(timeout_sec=10.0):
            raise SystemExit(f"service unavailable: {client.srv_name}")

    arm = ArmSystem.Request()
    arm.operator_id = "stream-probe"
    arm.checklist_hash = "stream-probe"
    arm.confirm_phrase = "I_HAVE_A_CLEAR_WORKSPACE_AND_SUPPORT"
    arm.contract.major = 0
    arm.contract.minor = 1
    arm.contract.patch = 0
    arm.contract.contract_hash = CONTRACT
    arm.profile_hash = ""
    future = arm_client.call_async(arm)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    print("arm", future.result().armed, future.result().message, flush=True)

    lease = AcquireLease.Request()
    lease.holder = "stream-probe"
    lease.scope = "arm"
    lease.mode = "exclusive"
    lease.priority = 40
    lease.ttl_ms = 30_000
    lease.deadman_required = True
    lease.contract.major = 0
    lease.contract.minor = 1
    lease.contract.patch = 0
    lease.contract.contract_hash = CONTRACT
    future = lease_client.call_async(lease)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    granted = future.result()
    print("lease", granted.granted, granted.lease.lease_id, granted.reason_code, flush=True)
    if not granted.granted:
        return 1
    lease_id = granted.lease.lease_id

    deadline = time.monotonic() + 5.0
    while latest["first"] is None and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)

    pub = node.create_publisher(TeleopCommand, "/jcheng/intent/teleop", qos)
    frames = max(1, int(args.duration_s * args.rate_hz))
    for index in range(frames):
        message = TeleopCommand()
        message.header.stamp = node.get_clock().now().to_msg()
        message.seq = index
        message.lease_id = lease_id
        message.source = "stream-probe"
        message.contract.major = 0
        message.contract.minor = 1
        message.contract.patch = 0
        message.contract.contract_hash = CONTRACT
        message.mode = "JOINT_VELOCITY"
        message.joint_velocity_rad_s = list(velocities)
        message.deadman = True
        pub.publish(message)
        rclpy.spin_once(node, timeout_sec=0.0)
        time.sleep(1.0 / args.rate_hz)

    neutral = TeleopCommand()
    neutral.header.stamp = node.get_clock().now().to_msg()
    neutral.seq = frames
    neutral.lease_id = lease_id
    neutral.source = "stream-probe"
    neutral.contract.major = 0
    neutral.contract.minor = 1
    neutral.contract.patch = 0
    neutral.contract.contract_hash = CONTRACT
    neutral.mode = "JOINT_VELOCITY"
    neutral.joint_velocity_rad_s = [0.0] * args.joints
    neutral.deadman = False
    pub.publish(neutral)
    for _ in range(20):
        rclpy.spin_once(node, timeout_sec=0.05)

    first = latest["first"]
    last = latest["last"]
    result = {
        "frames": frames,
        "first": first,
        "last": last,
        "delta": [round(b - a, 6) for a, b in zip(first, last)] if first and last else None,
        "moved_joints": sum(
            1 for a, b in zip(first, last) if abs(b - a) > 1e-6
        ) if first and last else 0,
    }
    print(json.dumps(result, sort_keys=True))

    release_client = node.create_client(ReleaseLease, "/jcheng/authority/release_lease")
    if release_client.wait_for_service(timeout_sec=5.0):
        request = ReleaseLease.Request()
        request.lease_id = lease_id
        future = release_client.call_async(request)
        rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
    node.destroy_node()
    rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
