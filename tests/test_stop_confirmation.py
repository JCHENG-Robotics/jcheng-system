"""R5 stop-confirmation levels in the core and HTTP gateway."""

from __future__ import annotations

import json
from pathlib import Path

from jcheng_system_core.gateway import GatewayApp
from jcheng_system_core.stack import MockStack

TOKEN = "test-token"


def _stack(root: Path) -> MockStack:
    return MockStack.build(root)


def test_idle_stop_is_request_accepted(root: Path) -> None:
    stack = _stack(root)
    result = stack.arbiter.stop("operator", "idle stop")
    assert result.accepted is True
    assert result.confirmation == "request_accepted"
    assert result.physical_stop == "unverified"
    assert result.safety.stop_latched is True


def test_stop_during_active_motion_is_backend_confirmed(root: Path) -> None:
    stack = _stack(root)
    stack.arm()
    lease = stack.lease(scope="full")
    command = stack.command(lease_id=lease.lease_id)
    assert stack.arbiter.start(command) is None
    result = stack.arbiter.stop("operator", "active stop")
    assert result.accepted is True
    assert result.confirmation == "backend_confirmed"
    assert result.physical_stop == "unverified"
    assert result.safety.stop_latched is True


def test_gateway_stop_response_carries_confirmation(root: Path) -> None:
    stack = _stack(root)
    app = GatewayApp(stack, TOKEN, max_requests_per_second=100)
    headers = {"authorization": f"Bearer {TOKEN}"}
    body = json.dumps({"operator_id": "operator", "reason": "gateway stop"}).encode()
    status, payload = app.handle("POST", "/v1/motion/stop", headers, body)
    assert status == 200
    assert payload["accepted"] is True
    assert payload["confirmation"] == "request_accepted"
    assert payload["physical_stop"] == "unverified"
