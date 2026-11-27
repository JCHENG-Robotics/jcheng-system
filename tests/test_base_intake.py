"""Gated base intake tests for the exact mobile provisional envelope.

The fixture in ``tests/fixtures/mobile_base_intent_v1.json`` has exactly the
mobile ``intent.py`` ``to_dict`` shape: schema, lease_id, holder, command_id,
seq, stamp_ns, frame, nested linear/angular and duty_limit. It contains no
contract/deadman/source/max_duty fields. Rejections must never reach the sink;
every stop path writes the zero command.

Fault-injection coverage: valid envelope, missing/expired lease, wrong
holder/scope, stale/future timestamp, duplicate/old seq, unknown schema,
flat/legacy shape, bad JSON, disabled intake, wrong domain, duty clamping,
stop priority and a refusing hardware sink.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcheng_system_core.authority import (
    GatewayLeaseView,
    LeaseManager,
    RejectAllLeaseView,
)
from jcheng_system_core.base import (
    ARMED,
    DISABLED,
    FAULT,
    IDLE,
    MOVING,
    STOP_LATCHED,
    BaseAuthority,
    BaseConfig,
    base_sink_writer,
)
from jcheng_system_core.base_intake import (
    BASE_INTENT_SCHEMA,
    BASE_INTENT_TOPIC,
    BaseIntake,
    DISABLED as INTAKE_DISABLED,
    FUTURE_TIMESTAMP,
    INVALID_ENVELOPE,
    STALE_TIMESTAMP,
    UnavailableBaseBridge,
    parse_base_intent,
    resolve_lease_source,
)
from jcheng_system_core.errors import ErrorCode
from jcheng_system_core.guard import PublisherGuard
from jcheng_system_core.hardware_authority import HardwareAuthority
from jcheng_system_core.stack import MockStack
from jcheng_system_core.timebase import ManualClock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mobile_base_intent_v1.json"
STAMP_NS = 1_000_000_000
AGE_NS = 10_000_000


class RecordingSink:
    def __init__(self) -> None:
        self.outputs: list[tuple[object, object]] = []

    def __call__(self, intent, command) -> None:
        self.outputs.append((intent, command))

    @property
    def last(self):
        return self.outputs[-1][1] if self.outputs else None

    @property
    def count(self) -> int:
        return len(self.outputs)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _envelope(
    lease_id: str,
    *,
    seq: int = 1,
    command_id: str | None = None,
    holder: str = "nav2",
    stamp_ns: int = STAMP_NS,
    frame: str = "base_link",
    duty_limit: float = 1.0,
    linear_x: float = 0.05,
    linear_y: float = 0.0,
    angular_z: float = 0.0,
    schema: str = BASE_INTENT_SCHEMA,
) -> str:
    value = _fixture()
    value.update(
        {
            "schema": schema,
            "lease_id": lease_id,
            "holder": holder,
            "command_id": command_id or f"cmd-{seq}",
            "seq": seq,
            "stamp_ns": stamp_ns,
            "frame": frame,
            "linear": {"x": linear_x, "y": linear_y},
            "angular": {"z": angular_z},
            "duty_limit": duty_limit,
        }
    )
    return json.dumps(value)


def _setup(
    *,
    enabled: bool = True,
    scope: str = "base",
    ttl_ms: int = 30_000,
    domain_id: int | None = 42,
    required_domain_id: int = 42,
):
    clock = ManualClock()
    stack = MockStack.build(ROOT, clock=clock)
    sink = RecordingSink()
    base = BaseAuthority(
        authority=stack.authority,
        clock=clock,
        sink=sink,
        config=BaseConfig(enabled=enabled),
        safety=stack.safety,
        contract=stack.version,
    )
    lease = stack.authority.acquire(
        holder="nav2",
        scope=scope,
        mode="exclusive",
        priority=50,
        ttl_ms=ttl_ms,
        deadman_required=False,
        contract=stack.version,
    )
    intake = BaseIntake(
        base,
        clock,
        contract=stack.version,
        enabled=enabled,
        domain_id=domain_id,
        required_domain_id=required_domain_id,
    )
    return stack, base, sink, intake, lease


def _handle(intake: BaseIntake, payload: str, *, stamp_ns: int = STAMP_NS, age_ns: int = AGE_NS):
    return intake.handle(payload, receive_time_ns=stamp_ns + age_ns)


def test_mobile_fixture_has_exactly_the_mobile_shape() -> None:
    value = _fixture()
    assert set(value) == {
        "schema",
        "lease_id",
        "holder",
        "command_id",
        "seq",
        "stamp_ns",
        "frame",
        "linear",
        "angular",
        "duty_limit",
    }
    assert value["schema"] == BASE_INTENT_SCHEMA
    assert set(value["linear"]) == {"x", "y"}
    assert set(value["angular"]) == {"z"}
    assert isinstance(value["stamp_ns"], int)
    for absent in ("contract", "deadman_ms", "source", "max_duty_normalized"):
        assert absent not in value


def test_valid_mobile_envelope_is_accepted() -> None:
    _stack, base, sink, intake, lease = _setup()
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is True
    assert result.code == ErrorCode.OK.value
    assert result.state == MOVING
    assert base.state == MOVING
    assert sink.last.is_zero is False
    assert sink.last.duty == pytest.approx((0.1, 0.1, 0.1, 0.1))
    status = intake.status()
    assert status["topic"] == BASE_INTENT_TOPIC
    assert status["enabled"] is True
    assert status["counters"] == {"received": 1, "accepted": 1, "rejected": 0}


def test_flat_legacy_envelope_is_rejected() -> None:
    _stack, _base, sink, intake, lease = _setup()
    flat = {
        "schema": BASE_INTENT_SCHEMA,
        "lease_id": lease.lease_id,
        "holder": "nav2",
        "command_id": "cmd-flat",
        "seq": 1,
        "stamp": 1.0,
        "linear_x_mps": 0.05,
        "linear_y_mps": 0.0,
        "angular_z_radps": 0.0,
        "max_duty_normalized": 0.1,
        "deadman_ms": 300,
        "source": "mobile",
    }
    result = _handle(intake, json.dumps(flat))
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert "frame" in result.message
    assert sink.count == 0


def test_unknown_schema_is_rejected() -> None:
    _stack, _base, sink, intake, lease = _setup()
    result = _handle(
        intake, _envelope(lease.lease_id, schema="jcheng.mobile.base_intent.v2")
    )
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert BASE_INTENT_SCHEMA in result.message
    assert sink.count == 0


def test_wrong_frame_is_rejected() -> None:
    _stack, _base, sink, intake, lease = _setup()
    result = _handle(intake, _envelope(lease.lease_id, frame="odom"))
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert "frame" in result.message
    assert sink.count == 0


def test_missing_lease_latches_and_zeroes() -> None:
    _stack, base, sink, intake, _lease = _setup()
    result = _handle(intake, _envelope("lease-missing"))
    assert result.accepted is False
    assert result.code == ErrorCode.NO_LEASE.value
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True


def test_expired_lease_latches_and_zeroes() -> None:
    stack, base, sink, intake, lease = _setup(ttl_ms=100)
    stack.clock.advance(0.2)
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == ErrorCode.LEASE_EXPIRED.value
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True


def test_wrong_holder_latches_and_zeroes() -> None:
    _stack, base, sink, intake, lease = _setup()
    result = _handle(intake, _envelope(lease.lease_id, holder="intruder"))
    assert result.accepted is False
    assert result.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True


def test_wrong_scope_latches_and_zeroes() -> None:
    _stack, base, sink, intake, lease = _setup(scope="arm")
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == ErrorCode.NOT_LEASE_HOLDER.value
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True


def test_stale_stamp_ns_is_rejected_without_a_write() -> None:
    _stack, base, sink, intake, lease = _setup()
    result = _handle(intake, _envelope(lease.lease_id), age_ns=200_000_001)
    assert result.accepted is False
    assert result.code == STALE_TIMESTAMP
    assert base.state == IDLE
    assert sink.count == 0


def test_future_stamp_ns_is_rejected_without_a_write() -> None:
    _stack, _base, sink, intake, lease = _setup()
    result = _handle(intake, _envelope(lease.lease_id), age_ns=-50_000_001)
    assert result.accepted is False
    assert result.code == FUTURE_TIMESTAMP
    assert sink.count == 0


def test_duplicate_and_old_seq_do_not_write() -> None:
    _stack, _base, sink, intake, lease = _setup()
    assert _handle(intake, _envelope(lease.lease_id, seq=5)).accepted is True
    count = sink.count
    duplicate = _handle(
        intake, _envelope(lease.lease_id, seq=5, command_id="cmd-duplicate")
    )
    assert duplicate.accepted is False
    assert duplicate.code == ErrorCode.INVALID_COMMAND.value
    assert sink.count == count

    old = _handle(intake, _envelope(lease.lease_id, seq=4))
    assert old.accepted is False
    assert sink.count == count

    fresh = _handle(intake, _envelope(lease.lease_id, seq=6))
    assert fresh.accepted is True
    assert fresh.state == MOVING
    assert sink.count == count + 1


def test_duty_limit_is_clamped() -> None:
    _stack, base, sink, intake, lease = _setup()
    high = _handle(intake, _envelope(lease.lease_id, duty_limit=5.0))
    assert high.accepted is True
    assert sink.last.duty == pytest.approx((0.1, 0.1, 0.1, 0.1))

    low = _handle(
        intake,
        _envelope(lease.lease_id, seq=2, command_id="cmd-2", duty_limit=-1.0),
    )
    assert low.accepted is True
    assert low.state == ARMED
    assert sink.last.is_zero is True
    assert base.state == ARMED


def test_disabled_intake_rejects_and_never_submits() -> None:
    _stack, base, sink, intake, lease = _setup(enabled=False)
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == INTAKE_DISABLED
    assert result.state == DISABLED
    assert sink.count == 0
    assert base.state == DISABLED
    assert intake.status()["enabled"] is False


def test_wrong_domain_disables_the_intake() -> None:
    _stack, _base, sink, intake, lease = _setup(domain_id=0)
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == "WRONG_DOMAIN"
    assert sink.count == 0
    assert intake.status()["reason"] == "WRONG_DOMAIN"


def test_stop_priority_blocks_later_valid_envelopes() -> None:
    _stack, base, sink, intake, lease = _setup()
    assert _handle(intake, _envelope(lease.lease_id, seq=1)).accepted is True
    base.stop("operator", "operator stop")
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True

    later = _handle(intake, _envelope(lease.lease_id, seq=2))
    assert later.accepted is False
    assert later.code == ErrorCode.SAFETY_STOP_LATCHED.value
    assert sink.last.is_zero is True

    malformed = intake.handle("{bad", receive_time_ns=STAMP_NS + AGE_NS)
    assert malformed.accepted is False
    assert base.state == STOP_LATCHED
    assert sink.last.is_zero is True


def test_bad_json_fails_closed() -> None:
    _stack, base, sink, intake, _lease = _setup()
    result = _handle(intake, "{not-json")
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert base.state == IDLE
    assert sink.count == 0


def test_nonfinite_and_bad_types_fail_closed() -> None:
    _stack, _base, sink, intake, lease = _setup()
    value = _fixture()
    value.update(
        {
            "lease_id": lease.lease_id,
            "stamp_ns": STAMP_NS,
            "linear": {"x": float("nan"), "y": 0.0},
        }
    )
    result = _handle(intake, json.dumps(value))
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert sink.count == 0

    value = _fixture()
    value.update(
        {
            "lease_id": lease.lease_id,
            "stamp_ns": STAMP_NS,
            "seq": True,
        }
    )
    result = _handle(intake, json.dumps(value))
    assert result.accepted is False
    assert result.code == INVALID_ENVELOPE
    assert sink.count == 0


def test_parse_returns_the_local_contract_and_monotonic_stamp() -> None:
    stack, _base, _sink, _intake, lease = _setup()
    intent = parse_base_intent(
        _envelope(lease.lease_id),
        contract=stack.version,
        receive_time_ns=STAMP_NS + AGE_NS,
    )
    assert intent.contract == stack.version
    assert intent.stamp_s == pytest.approx(STAMP_NS / 1e9)
    assert intent.source == "jcheng-mobile"
    assert intent.deadman_ms == 0


def test_refusing_sink_does_not_fabricate_a_base_output() -> None:
    clock = ManualClock()
    stack = MockStack.build(ROOT, clock=clock)
    hardware = HardwareAuthority(
        contract=stack.version,
        authority=stack.authority,
        safety=stack.safety,
        clock=clock,
        bridge=UnavailableBaseBridge(),
        guard=PublisherGuard(allowed={"jcheng_base"}),
        max_base_duty=0.10,
        base_writes_enabled=True,
    )
    base = BaseAuthority(
        authority=stack.authority,
        clock=clock,
        sink=base_sink_writer(hardware.submit_base, hardware.zero_base),
        config=BaseConfig(enabled=True),
        safety=stack.safety,
        contract=stack.version,
    )
    lease = stack.authority.acquire(
        holder="nav2",
        scope="base",
        mode="exclusive",
        priority=50,
        ttl_ms=30_000,
        deadman_required=False,
        contract=stack.version,
    )
    intake = BaseIntake(
        base, clock, contract=stack.version, enabled=True, domain_id=42
    )
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == ErrorCode.HARDWARE_UNAVAILABLE.value
    assert base.state == FAULT
    assert stack.safety.stop_latched is True
    assert hardware.status()["last_base_duty"] == [0.0, 0.0, 0.0, 0.0]


def _gateway_view_setup():
    stack = MockStack.build(ROOT, clock=ManualClock())
    view = GatewayLeaseView(stack.authority)
    sink = RecordingSink()
    intake = BaseIntake(
        clock=stack.clock,
        lease_view=view,
        contract=stack.version,
        safety=stack.safety,
        sink=sink,
        enabled=True,
        domain_id=42,
        lease_source="gateway",
    )
    return stack, view, sink, intake


def _base_lease(stack: MockStack):
    return stack.authority.acquire(
        holder="nav2",
        scope="base",
        mode="exclusive",
        priority=50,
        ttl_ms=30_000,
        deadman_required=False,
        contract=stack.version,
    )


def test_gateway_lease_view_shares_the_gateway_manager() -> None:
    stack, view, sink, intake = _gateway_view_setup()
    lease = _base_lease(stack)
    assert intake.lease_view is view
    assert intake.base.authority is view
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is True
    assert result.state == MOVING
    assert sink.last.is_zero is False

    # The view is live, not a copy: revoking through the gateway manager is
    # immediately visible to the intake.
    stack.authority.revoke(lease.lease_id, "operator", "test revoke", stack.version)
    revoked = _handle(intake, _envelope(lease.lease_id, seq=2))
    assert revoked.accepted is False
    assert revoked.code == ErrorCode.LEASE_REVOKED.value
    assert sink.last.is_zero is True


def test_separate_manager_lease_is_rejected() -> None:
    stack, _view, sink, intake = _gateway_view_setup()
    other = LeaseManager(stack.version, stack.clock)
    foreign = other.acquire(
        holder="nav2",
        scope="base",
        mode="exclusive",
        priority=50,
        ttl_ms=30_000,
        deadman_required=False,
        contract=stack.version,
    )
    assert other.check(foreign.lease_id, "base").ok is True
    assert stack.authority.check(foreign.lease_id, "base").code == ErrorCode.NO_LEASE.value

    result = _handle(intake, _envelope(foreign.lease_id))
    assert result.accepted is False
    assert result.code == ErrorCode.NO_LEASE.value
    # No desired frame: the only sink output is the latched zero.
    assert sink.count == 1
    assert sink.last.is_zero is True


def test_base_intake_requires_an_injected_lease_view() -> None:
    with pytest.raises(ValueError):
        BaseIntake()
    with pytest.raises(ValueError):
        BaseIntake(clock=ManualClock())

    stack = MockStack.build(ROOT, clock=ManualClock())
    base = BaseAuthority(
        authority=stack.authority,
        clock=stack.clock,
        config=BaseConfig(enabled=True),
        safety=stack.safety,
        contract=stack.version,
    )
    other = LeaseManager(stack.version, stack.clock)
    with pytest.raises(ValueError):
        BaseIntake(base, stack.clock, lease_view=GatewayLeaseView(other))


def test_reject_all_lease_view_never_falls_back_to_a_local_lease() -> None:
    stack = MockStack.build(ROOT, clock=ManualClock())
    lease = _base_lease(stack)
    sink = RecordingSink()
    intake = BaseIntake(
        clock=stack.clock,
        lease_view=RejectAllLeaseView(stack.version),
        contract=stack.version,
        safety=stack.safety,
        sink=sink,
        enabled=True,
        domain_id=42,
        lease_source="none",
    )
    result = _handle(intake, _envelope(lease.lease_id))
    assert result.accepted is False
    assert result.code == ErrorCode.NO_LEASE.value
    assert sink.count == 1
    assert sink.last.is_zero is True
    status = intake.status()
    assert status["lease_source"] == "none"
    assert status["lease_view"] == "RejectAllLeaseView"


def test_resolve_lease_source_defaults_and_unknowns_fail_closed() -> None:
    stack = MockStack.build(ROOT, clock=ManualClock())
    view, origin = resolve_lease_source("none", None, stack.version)
    assert isinstance(view, RejectAllLeaseView)
    assert origin == "none"

    view, origin = resolve_lease_source("gateway", None, stack.version)
    assert isinstance(view, RejectAllLeaseView)
    assert origin == "gateway-unavailable"

    shared = GatewayLeaseView(stack.authority)
    view, origin = resolve_lease_source("gateway", shared, stack.version)
    assert view is shared
    assert origin == "gateway"

    view, origin = resolve_lease_source("bogus", None, stack.version)
    assert isinstance(view, RejectAllLeaseView)
    assert origin == "invalid"


def test_node_source_is_disabled_by_default_and_does_not_fabricate() -> None:
    source = (
        ROOT
        / "ros2_ws"
        / "src"
        / "jcheng_base"
        / "jcheng_base"
        / "node.py"
    ).read_text(encoding="utf-8")
    assert 'declare_parameter("enabled", False)' in source
    assert 'declare_parameter("lease_source", "none")' in source
    assert "BASE_INTENT_TOPIC" in source
    assert "BaseIntake(" in source
    assert "resolve_lease_source(" in source
    assert "shared_lease_view: LeaseView | None" in source
    assert "LeaseManager(" not in source
    assert "UnavailableBaseBridge()" in source
    assert "def publish_motor_speeds" not in source

    launch_dir = ROOT / "ros2_ws" / "src" / "jcheng_bringup" / "launch"
    launch_text = "\n".join(
        path.read_text(encoding="utf-8") for path in launch_dir.glob("*.py")
    )
    assert "jcheng_base" not in launch_text
