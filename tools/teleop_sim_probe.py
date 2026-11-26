"""L3 probe: teleop intent -> system intake -> simulation bridge.

The runtime must be started with `sim_bridge:=true` and a `robot` whose profile
joint count matches `--joints`. The probe arms the system, acquires a
priority-40 arm lease, publishes a short teleop velocity script, sends a
neutral frame, and prints the simulated joint positions. Test-only; no hardware.

Typical runs
------------

ArmPi or PiPER X with the probe and runtime on the same domain (43):

    ROS_DOMAIN_ID=43 python3 tools/teleop_sim_probe.py \
        --joints 5 --velocity 0.5,0,0,0,0

Canonical capability domain: the probe/runtime on 42 and the plant on 43.
Set ROS_DOMAIN_ID=42 for the process and pass `--sim-domain 43`; the joint
state subscriber then runs in a second rclpy context on domain 43:

    ROS_DOMAIN_ID=42 python3 tools/teleop_sim_probe.py \
        --joints 6 --velocity 0.5,-0.5,0.5,-0.5,0.5,-0.5 --sim-domain 43
"""

from __future__ import annotations

import argparse
import math
import threading
import time

import rclpy
from rclpy.context import Context
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from jcheng_interfaces.msg import TeleopCommand
from jcheng_interfaces.srv import AcquireLease, ArmSystem, ReleaseLease
from sensor_msgs.msg import JointState

CONTRACT = "sha256:0b8d72ced7f54d444d6851d8b99a51a78e2bed3f687324f51a15f76557f24b42"


def _parse_velocity(raw: str, joints: int) -> list[float]:
    values = [float(item) for item in raw.split(",") if item.strip()]
    if len(values) != joints:
        raise SystemExit(
            f"--velocity has {len(values)} entries but --joints is {joints}"
        )
    if not all(math.isfinite(value) for value in values):
        raise SystemExit("--velocity must be finite")
    return values


class _StateSink(Node):
    """Latest `/sim/joint_states`, optionally on a separate domain."""

    def __init__(self, node_name: str, context: Context | None = None) -> None:
        super().__init__(node_name, context=context)
        self.latest: JointState | None = None
        self.first: list[float] | None = None
        qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        self.create_subscription(JointState, "/sim/joint_states", self._on_state, qos)

    def _on_state(self, msg: JointState) -> None:
        if self.first is None:
            self.first = list(msg.position)
        self.latest = msg


def _start_state_sink(
    sim_domain: int | None,
) -> tuple[_StateSink, Context | None, SingleThreadedExecutor | None, threading.Thread | None]:
    if sim_domain is None:
        return _StateSink("teleop_sim_probe_state"), None, None, None
    context = Context()
    rclpy.init(context=context, domain_id=sim_domain)
    sink = _StateSink("teleop_sim_probe_state", context=context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(sink)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    return sink, context, executor, thread


def _keep_spinning(node: Node, sink: _StateSink, duration_s: float) -> None:
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)


def _frame(
    node: Node,
    *,
    seq: int,
    lease_id: str,
    velocities: list[float],
    deadman: bool,
) -> TeleopCommand:
    message = TeleopCommand()
    message.header.stamp = node.get_clock().now().to_msg()
    message.seq = seq
    message.lease_id = lease_id
    message.source = "probe"
    message.contract.major = 0
    message.contract.minor = 1
    message.contract.patch = 0
    message.contract.contract_hash = CONTRACT
    message.mode = "JOINT_VELOCITY"
    message.joint_velocity_rad_s = list(velocities)
    message.deadman = bool(deadman)
    return message


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--joints", type=int, required=True, help="profile joint count")
    parser.add_argument("--velocity", default="", help="comma-separated rad/s per joint")
    parser.add_argument("--duration-s", type=float, default=0.8)
    parser.add_argument("--rate-hz", type=float, default=50.0)
    parser.add_argument(
        "--sim-domain",
        type=int,
        default=None,
        help="read /sim/joint_states in a second context on this domain",
    )
    parser.add_argument(
        "--release-frames",
        type=int,
        default=4,
        help="trailing frames sent with deadman=false",
    )
    parser.add_argument("--state-timeout-s", type=float, default=5.0)
    parser.add_argument(
        "--confirm-phrase",
        default="I_HAVE_A_CLEAR_WORKSPACE_AND_SUPPORT",
    )
    args = parser.parse_args(argv)
    if args.joints <= 0:
        raise SystemExit("--joints must be positive")
    if args.rate_hz <= 0 or args.duration_s <= 0:
        raise SystemExit("--rate-hz and --duration-s must be positive")
    velocities = _parse_velocity(
        args.velocity or ",".join(["0.5"] + ["0.0"] * (args.joints - 1)), args.joints
    )

    rclpy.init()
    node = Node("teleop_sim_probe")
    sink, sim_context, sim_executor, sim_thread = _start_state_sink(args.sim_domain)
    try:
        arm_client = node.create_client(ArmSystem, "/jcheng/safety/arm")
        lease_client = node.create_client(AcquireLease, "/jcheng/authority/acquire_lease")
        for client in (arm_client, lease_client):
            if not client.wait_for_service(timeout_sec=10.0):
                raise SystemExit(f"service unavailable: {client.srv_name}")

        arm = ArmSystem.Request()
        arm.operator_id = "probe"
        arm.checklist_hash = "probe"
        arm.confirm_phrase = args.confirm_phrase
        arm.contract.major = 0
        arm.contract.minor = 1
        arm.contract.patch = 0
        arm.contract.contract_hash = CONTRACT
        arm.profile_hash = ""
        arm_future = arm_client.call_async(arm)
        rclpy.spin_until_future_complete(node, arm_future, timeout_sec=10.0)
        arm_resp = arm_future.result()
        print("arm", arm_resp.armed, arm_resp.message)
        if not arm_resp.armed:
            raise SystemExit("arm rejected")

        lease = AcquireLease.Request()
        lease.holder = "probe"
        lease.scope = "arm"
        lease.mode = "exclusive"
        lease.priority = 40
        lease.ttl_ms = 30_000
        lease.deadman_required = True
        lease.contract.major = 0
        lease.contract.minor = 1
        lease.contract.patch = 0
        lease.contract.contract_hash = CONTRACT
        lease_future = lease_client.call_async(lease)
        rclpy.spin_until_future_complete(node, lease_future, timeout_sec=10.0)
        lease_resp = lease_future.result()
        print("lease", lease_resp.granted, lease_resp.lease.lease_id, lease_resp.reason_code)
        if not lease_resp.granted:
            raise SystemExit("lease rejected")
        lease_id = lease_resp.lease.lease_id

        # Let the state subscription see the initial pose before commanding.
        deadline = time.monotonic() + args.state_timeout_s
        while sink.first is None and time.monotonic() < deadline:
            time.sleep(0.05)

        qos = QoSProfile(
            depth=1,
            history=HistoryPolicy.KEEP_LAST,
            reliability=ReliabilityPolicy.BEST_EFFORT,
        )
        pub = node.create_publisher(TeleopCommand, "/jcheng/intent/teleop", qos)
        frames = max(1, int(args.duration_s * args.rate_hz))
        release = min(args.release_frames, frames)
        period = 1.0 / args.rate_hz
        for index in range(frames):
            deadman = index < frames - release
            pub.publish(
                _frame(
                    node,
                    seq=index,
                    lease_id=lease_id,
                    velocities=velocities,
                    deadman=deadman,
                )
            )
            rclpy.spin_once(node, timeout_sec=0.0)
            time.sleep(period)

        # Explicit neutral frame closes the script; the intake treats
        # deadman=false as a non-latching session abort.
        pub.publish(
            _frame(
                node,
                seq=frames,
                lease_id=lease_id,
                velocities=[0.0] * args.joints,
                deadman=False,
            )
        )
        _keep_spinning(node, sink, 1.0)

        if sink.first is not None:
            print("initial_positions", list(sink.first))
        if sink.latest is not None:
            print("sim_positions", list(sink.latest.position))
        else:
            print("sim_positions", "no state")

        release_client = node.create_client(ReleaseLease, "/jcheng/authority/release_lease")
        if release_client.wait_for_service(timeout_sec=5.0):
            request = ReleaseLease.Request()
            request.lease_id = lease_id
            future = release_client.call_async(request)
            rclpy.spin_until_future_complete(node, future, timeout_sec=5.0)
        return 0
    finally:
        node.destroy_node()
        if sim_executor is not None:
            sim_executor.shutdown()
        if sim_thread is not None:
            sim_thread.join(timeout=2.0)
        if sim_context is not None:
            sim_context.try_shutdown()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
