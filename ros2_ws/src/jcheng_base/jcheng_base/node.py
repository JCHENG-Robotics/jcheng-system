"""Thin, gated base intent intake node (test-only, disabled by default).

Subscribes to ``/jcheng/intent/base`` (domain 42) with the provisional mobile
JSON envelope ``jcheng.mobile.base_intent.v1`` and forwards it to
``BaseAuthority.submit`` through ``BaseIntake``. The node does not expose a
motion service and does not fabricate a base output: the default
``UnavailableBaseBridge`` refuses every motor-speed write until a reviewed
base bridge exists. No launch file enables this node.
"""

from __future__ import annotations

import os
from pathlib import Path
import time

from rclpy.node import Node
from std_msgs.msg import String

from jcheng_system_core.authority import LeaseView
from jcheng_system_core.base import BaseConfig, base_sink_writer
from jcheng_system_core.base_intake import (
    BASE_INTENT_DOMAIN_ID,
    BASE_INTENT_TOPIC,
    DEFAULT_MAX_AGE_NS,
    DEFAULT_MAX_FUTURE_NS,
    BaseIntake,
    UnavailableBaseBridge,
    resolve_lease_source,
)
from jcheng_system_core.config import RobotProfile, find_repo_root, load_contract
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.hardware_authority import HardwareAuthority
from jcheng_system_core.safety import SafetyLimits, SafetySupervisor
from jcheng_system_core.timebase import Clock


def _domain_from_env() -> int | None:
    raw = os.environ.get("ROS_DOMAIN_ID")
    if raw is None or not raw.strip():
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class BaseIntakeNode(Node):
    def __init__(self, *, shared_lease_view: LeaseView | None = None) -> None:
        super().__init__("jcheng_base_intake")
        self.declare_parameter("enabled", False)
        self.declare_parameter("lease_source", "none")
        self.declare_parameter("topic", BASE_INTENT_TOPIC)
        self.declare_parameter("required_domain_id", BASE_INTENT_DOMAIN_ID)
        self.declare_parameter("max_age_ns", DEFAULT_MAX_AGE_NS)
        self.declare_parameter("max_future_ns", DEFAULT_MAX_FUTURE_NS)
        self.declare_parameter("intent_timeout_ms", 200.0)
        self.declare_parameter("root", "")

        enabled = bool(self.get_parameter("enabled").value)
        lease_source = str(self.get_parameter("lease_source").value)
        topic = str(self.get_parameter("topic").value)
        required_domain = int(self.get_parameter("required_domain_id").value)
        max_age_ns = int(self.get_parameter("max_age_ns").value)
        max_future_ns = int(self.get_parameter("max_future_ns").value)
        intent_timeout_ms = float(self.get_parameter("intent_timeout_ms").value)
        root_value = str(
            self.get_parameter("root").value or os.environ.get("JCHENG_ROOT", "")
        )
        self.root = Path(root_value).expanduser() if root_value else find_repo_root()

        self.profile = RobotProfile.from_file(
            self.root / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
        )
        self.contract = load_contract(self.root / "configs" / "contract.yaml")
        self.clock = Clock()
        overlay = (
            self.root / "configs" / "robots" / "armpi_ultra" / "safety_overlay.yaml"
        )
        limits = SafetyLimits.from_file(overlay) if overlay.is_file() else SafetyLimits()
        self.safety = SafetySupervisor(
            self.profile, self.contract.version, self.clock, limits
        )
        base_profile = self.profile.profiles.get("base", {})
        max_duty = float(base_profile.get("max_abs_duty_normalized", 0.10))
        # Unified lease: the node never creates a LeaseManager. It either gets
        # the gateway's own view injected in-process or a reject-all view.
        self.lease_view, self.lease_origin = resolve_lease_source(
            lease_source, shared_lease_view, self.contract.version
        )
        # No reviewed base bridge exists yet; every write refuses rather than
        # fabricating a duty frame. Offline tests inject a recording sink.
        self.hardware = HardwareAuthority(
            contract=self.contract.version,
            authority=self.lease_view,
            safety=self.safety,
            clock=self.clock,
            bridge=UnavailableBaseBridge(),
            guard=PublisherGuard(allowed={"jcheng_base"}),
            max_base_duty=max_duty,
            base_writes_enabled=True,
        )
        self.intake = BaseIntake(
            clock=self.clock,
            lease_view=self.lease_view,
            contract=self.contract.version,
            safety=self.safety,
            sink=base_sink_writer(self.hardware.submit_base, self.hardware.zero_base),
            base_config=BaseConfig(
                enabled=enabled,
                intent_timeout_ms=intent_timeout_ms,
                max_duty_normalized=max_duty,
            ),
            enabled=enabled,
            domain_id=_domain_from_env(),
            required_domain_id=required_domain,
            topic=topic,
            max_age_ns=max_age_ns,
            max_future_ns=max_future_ns,
            lease_source=lease_source if lease_source in ("gateway", "none") else "invalid",
        )
        self.base = self.intake.base
        self._last_code = ""
        self.create_subscription(String, topic, self._on_intent, 10)
        self.create_timer(0.02, self._tick)
        status = self.intake.status()
        if status["enabled"]:
            self.get_logger().info(
                f"base intake enabled on {topic} "
                f"(domain={status['domain_id']}, schema={status['schema']}, "
                f"lease_source={lease_source}/{self.lease_origin}, "
                "base bridge=unavailable/fail-closed)"
            )
        else:
            self.get_logger().warning(
                f"base intake disabled ({status['reason']}); messages are rejected "
                "and no base output is submitted"
            )
        if self.lease_origin != "gateway":
            self.get_logger().warning(
                "no shared gateway lease source is available in this process; "
                "all base envelopes will be rejected (cross-process lease sharing "
                "is a deployment decision and is not implemented)"
            )

    def _on_intent(self, message: String) -> None:
        result = self.intake.handle(message.data, receive_time_ns=time.monotonic_ns())
        if result.code != self._last_code:
            self._last_code = result.code
            level = (
                self.get_logger().info if result.accepted else self.get_logger().warning
            )
            level(
                f"base intent {'accepted' if result.accepted else 'rejected'}: "
                f"{result.code} state={result.state} {result.message}"
            )

    def _tick(self) -> None:
        self.intake.tick()


def main() -> int:
    import rclpy

    rclpy.init(args=None)
    node = None
    try:
        node = BaseIntakeNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
