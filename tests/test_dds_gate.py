"""DDS ExecuteMotion startup gate tests (pure, offline).

The gate must deny by default and only allow the privileged domain 41 with
both the explicit env opt-in and an enforced SROS2 marker. The vendor node
must call the gate before constructing the ActionServer, without deleting the
ActionServer code.
"""

from __future__ import annotations

from pathlib import Path

from jcheng_system_core.vendor_gate import (
    ALLOWED,
    DISABLED_BY_DEFAULT,
    DDS_ACTION_TOPIC,
    ENV_NOT_ONE,
    INVALID_DOMAIN,
    SROS2_MISSING,
    WRONG_DOMAIN,
    WRONG_TOPIC,
    dds_execute_allowed,
    domain_id_from_env,
    evaluate_dds_execute_gate,
    sros2_marker_present,
)

ROOT = Path(__file__).resolve().parents[1]

FULL_ENV = {
    "JCHENG_ALLOW_DDS_EXECUTE": "1",
    "ROS_DOMAIN_ID": "41",
    "ROS_SECURITY_ENABLE": "true",
    "ROS_SECURITY_KEYSTORE": "/tmp/jcheng-keystore",
    "ROS_SECURITY_STRATEGY": "Enforce",
}


def test_default_is_disabled() -> None:
    result = evaluate_dds_execute_gate({})
    assert result.allowed is False
    assert result.code == DISABLED_BY_DEFAULT
    assert dds_execute_allowed({}) is False


def test_opt_in_must_be_exactly_one() -> None:
    env = dict(FULL_ENV)
    for value in ("0", "true", "yes", "2", ""):
        env["JCHENG_ALLOW_DDS_EXECUTE"] = value
        result = evaluate_dds_execute_gate(env)
        assert result.allowed is False, value
        assert result.code == ENV_NOT_ONE, value


def test_missing_or_invalid_domain_is_denied() -> None:
    env = dict(FULL_ENV)
    del env["ROS_DOMAIN_ID"]
    assert evaluate_dds_execute_gate(env).code == INVALID_DOMAIN

    env["ROS_DOMAIN_ID"] = "not-a-number"
    assert evaluate_dds_execute_gate(env).code == INVALID_DOMAIN
    assert domain_id_from_env({"ROS_DOMAIN_ID": "abc"}) is None


def test_wrong_domains_are_denied() -> None:
    for domain in ("0", "40", "42", "43"):
        env = dict(FULL_ENV)
        env["ROS_DOMAIN_ID"] = domain
        result = evaluate_dds_execute_gate(env)
        assert result.allowed is False, domain
        assert result.code == WRONG_DOMAIN, domain
        assert result.domain_id == int(domain)


def test_sros2_marker_is_required() -> None:
    env = dict(FULL_ENV)
    del env["ROS_SECURITY_ENABLE"]
    assert evaluate_dds_execute_gate(env).code == SROS2_MISSING

    env = dict(FULL_ENV)
    del env["ROS_SECURITY_KEYSTORE"]
    assert evaluate_dds_execute_gate(env).code == SROS2_MISSING
    assert sros2_marker_present(env) is False

    env = dict(FULL_ENV)
    env["ROS_SECURITY_STRATEGY"] = "Permissive"
    assert evaluate_dds_execute_gate(env).code == SROS2_MISSING

    env = dict(FULL_ENV)
    env["ROS_SECURITY_ENABLE"] = "false"
    assert evaluate_dds_execute_gate(env).code == SROS2_MISSING


def test_wrong_topic_is_denied() -> None:
    result = evaluate_dds_execute_gate(FULL_ENV, topic="/cmd_vel")
    assert result.allowed is False
    assert result.code == WRONG_TOPIC


def test_full_gate_allows_only_the_expected_combination() -> None:
    result = evaluate_dds_execute_gate(FULL_ENV)
    assert result.allowed is True
    assert result.code == ALLOWED
    assert result.domain_id == 41
    assert result.topic == DDS_ACTION_TOPIC
    assert result.sros2 is True
    assert dds_execute_allowed(FULL_ENV) is True
    # An explicit domain override is re-evaluated, not trusted.
    assert evaluate_dds_execute_gate(FULL_ENV, domain_id=42).code == WRONG_DOMAIN


def test_vendor_node_checks_the_gate_before_the_action_server() -> None:
    source = (ROOT / "ros2_ws" / "src" / "jcheng_vendor" / "jcheng_vendor" / "node.py").read_text(
        encoding="utf-8"
    )
    assert "evaluate_dds_execute_gate(" in source
    assert "ActionServer(" in source
    assert "self._action_server = None" in source
    assert source.index("evaluate_dds_execute_gate(") < source.index("ActionServer(")
