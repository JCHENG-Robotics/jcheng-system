"""HTTP composition over the M5.2 hardware executor (offline fake bridge)."""

from __future__ import annotations

import json
from pathlib import Path

from jcheng_system_core.gateway import GatewayApp
from jcheng_system_core.stack import MockStack
from jcheng_vendor.gateway_stack import VendorStack

from test_vendor_executor_c2p import FakeBridge

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "vendor-stack-token"


def _headers() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}


def _vendor() -> tuple[VendorStack, FakeBridge, GatewayApp]:
    from jcheng_system_core.config import RobotProfile
    from jcheng_system_core.timebase import Clock

    profile = RobotProfile.from_file(
        ROOT / "configs" / "robots" / "armpi_ultra" / "profile.yaml"
    )
    bridge = FakeBridge(Clock(), profile)
    stack = VendorStack.build(ROOT, bridge, settle_s=0.0)
    return stack, bridge, GatewayApp(stack, TOKEN)


def test_health_advertises_hardware_and_ws_mode() -> None:
    _, _, app = _vendor()
    status, payload = app.handle("GET", "/v1/health", {})
    assert status == 200
    assert payload["capabilities"] == ["hardware"]
    assert app.ws_hello()["mode"] == "hardware"


def test_mock_stack_still_advertises_mock() -> None:
    stack = MockStack.build(ROOT)
    app = GatewayApp(stack, TOKEN)
    status, payload = app.handle("GET", "/v1/health", {})
    assert status == 200
    assert payload["capabilities"] == ["mock"]
    assert app.ws_hello()["mode"] == "mock"


def test_execute_over_http_reaches_the_vendor_executor() -> None:
    stack, bridge, app = _vendor()
    stack.arm()
    lease = stack.lease()
    command = stack.command(lease_id=lease.lease_id, duration_s=0.5)
    status, payload = app.handle(
        "POST",
        "/v1/motion/execute",
        _headers(),
        json.dumps(command.to_dict()).encode(),
    )
    assert status == 200, payload
    assert payload["outcome"] == "COMPLETED"
    assert payload["bus_verified"] is True
    assert "send_step" in bridge.events
