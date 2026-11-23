from __future__ import annotations

from pathlib import Path

from jcheng_hardware.readonly import MockReadOnlyBus, ReadOnlyBridge, ReadOnlyMapping
from jcheng_system_core.config import RobotProfile, load_contract
from jcheng_system_core.timebase import ManualClock
from jcheng_telemetry.core import TelemetryCache
from jcheng_telemetry.evidence import ReadOnlyEvidence
from jcheng_telemetry.gateway import ReadOnlyGatewayApp


def _app(root: Path) -> ReadOnlyGatewayApp:
    profile = RobotProfile.from_file(root / "configs" / "robots" / "armpi_ultra" / "profile.yaml")
    contract = load_contract(root / "configs" / "contract.yaml")
    mapping = ReadOnlyMapping.from_file(
        root / "configs" / "robots" / "armpi_ultra" / "readonly_mapping.yaml"
    )
    clock = ManualClock()
    cache = TelemetryCache(profile.joint_names, profile.profile_hash, clock)
    bus = MockReadOnlyBus({joint.servo_id: 500 for joint in mapping.joints})
    bus.set_pulse(mapping.gripper_joint.servo_id, 170)
    cache.update_measured(ReadOnlyBridge(mapping, bus.read_positions).read(0.2))
    cache.update_joint_state(profile.joint_names, [0.0] * len(profile.joint_names), at=0.2)
    cache.update_battery_mv(7400, at=0.2)
    cache.update_topic("/depth_cam/rgb/image_raw", at=0.2)
    cache.update_topic("/tf", at=0.2)
    return ReadOnlyGatewayApp(
        cache=cache,
        contract=contract.version.to_dict(),
        profile_hash=profile.profile_hash,
        config_hash=profile.config_hash,
        domain_id=0,
        evidence=ReadOnlyEvidence(clock),
        token="token",
        max_requests_per_second=100,
    )


def test_readonly_gateway_routes(root: Path) -> None:
    app = _app(root)
    assert app.handle("GET", "/v1/health", {})[0] == 200
    assert app.handle("GET", "/v1/state", {})[0] == 401
    status, state = app.handle("GET", "/v1/state", {"authorization": "Bearer token"})
    assert status == 200
    assert state["truth"] == "measured"
    assert state["verification"] == "bus"
    status, battery = app.handle("GET", "/v1/battery", {"authorization": "Bearer token"})
    assert status == 200
    assert battery["voltage_v"] == 7.4
    status, cameras = app.handle("GET", "/v1/cameras", {"authorization": "Bearer token"})
    assert status == 200
    assert "/depth_cam/rgb/image_raw" in cameras["cameras"]
    status, safety = app.handle("GET", "/v1/safety", {"authorization": "Bearer token"})
    assert status == 200
    assert safety["authoritative"] is False
    assert safety["safety"]["state"] == "DISARMED"


def test_readonly_gateway_rejects_every_mutation(root: Path) -> None:
    app = _app(root)
    headers = {"authorization": "Bearer token"}
    for method, path in (
        ("POST", "/v1/motion/execute"),
        ("POST", "/v1/motion/stop"),
        ("POST", "/v1/safety/arm"),
        ("POST", "/v1/leases"),
        ("DELETE", "/v1/leases/lease-1"),
        ("PUT", "/v1/state"),
    ):
        status, payload = app.handle(method, path, headers, b"{}")
        assert status == 403
        assert payload["error"]["code"] == "FORBIDDEN"
        assert payload["error"]["details"]["writes"] == 0


def test_readonly_gateway_unknown_route_and_rate_limit(root: Path) -> None:
    app = _app(root)
    headers = {"authorization": "Bearer token"}
    assert app.handle("GET", "/v1/unknown", headers)[0] == 404
    limited = ReadOnlyGatewayApp(
        cache=app.cache,
        contract=app.contract,
        profile_hash=app.profile_hash,
        config_hash=app.config_hash,
        domain_id=app.domain_id,
        evidence=app.evidence,
        token="token",
        max_requests_per_second=1,
    )
    assert limited.handle("GET", "/v1/state", headers)[0] == 200
    assert limited.handle("GET", "/v1/state", headers)[0] == 429
