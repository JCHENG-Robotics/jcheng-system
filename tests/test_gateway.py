from __future__ import annotations

import json
from pathlib import Path
import urllib.error
import urllib.request

from jcheng_system_core.gateway import GatewayApp, GatewayServer
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock


def _request(port: int, path: str, token: str | None = None) -> tuple[int, dict]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_gateway_auth_and_state(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(GatewayApp(stack, "token", max_requests_per_second=100)) as server:
        assert _request(server.port, "/v1/health")[0] == 200
        assert _request(server.port, "/v1/state")[0] == 401
        status, payload = _request(server.port, "/v1/state", "token")
        assert status == 200
        assert payload["truth"] == "measured"


def test_gateway_rate_limit(root: Path) -> None:
    stack = MockStack.build(root, clock=ManualClock())
    with GatewayServer(GatewayApp(stack, "token", max_requests_per_second=2)) as server:
        statuses = [_request(server.port, "/v1/state", "token")[0] for _ in range(3)]
        assert statuses[-1] == 429
