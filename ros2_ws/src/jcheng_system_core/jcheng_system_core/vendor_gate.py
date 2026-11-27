"""Pure startup gate for the vendor DDS ``ExecuteMotion`` ActionServer.

HTTP ``/v1/motion/*`` is the only external motion entry. The DDS action is an
internal/test transport and is disabled by default. It may start only when all
of the following hold:

* ``JCHENG_ALLOW_DDS_EXECUTE`` is exactly ``"1"``;
* the process domain is the privileged domain 41 (``ROS_DOMAIN_ID``);
* an SROS2 marker is present: ``ROS_SECURITY_ENABLE`` is truthy,
  ``ROS_SECURITY_KEYSTORE`` is non-empty and the strategy is ``Enforce``;
* the ActionServer topic is the expected ``/jcheng/motion/execute``.

This module is pure Python (no ROS import) so the gate is unit-testable and
the vendor node can call it before constructing the ActionServer.
"""

from __future__ import annotations

from collections.abc import Mapping
import os
from dataclasses import dataclass
from typing import Any

ALLOW_ENV = "JCHENG_ALLOW_DDS_EXECUTE"
PRIVILEGED_DOMAIN_ID = 41
DDS_ACTION_TOPIC = "/jcheng/motion/execute"

SROS2_ENABLE_ENV = "ROS_SECURITY_ENABLE"
SROS2_KEYSTORE_ENV = "ROS_SECURITY_KEYSTORE"
SROS2_STRATEGY_ENV = "ROS_SECURITY_STRATEGY"

ALLOWED = "ALLOWED"
DISABLED_BY_DEFAULT = "DISABLED_BY_DEFAULT"
ENV_NOT_ONE = "ENV_NOT_ONE"
INVALID_DOMAIN = "INVALID_DOMAIN"
WRONG_DOMAIN = "WRONG_DOMAIN"
WRONG_TOPIC = "WRONG_TOPIC"
SROS2_MISSING = "SROS2_MISSING"

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DdsGateResult:
    allowed: bool
    code: str
    message: str
    domain_id: int | None = None
    topic: str = ""
    sros2: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "message": self.message,
            "domain_id": self.domain_id,
            "topic": self.topic,
            "sros2": self.sros2,
        }


def domain_id_from_env(env: Mapping[str, str] | None = None) -> int | None:
    """Return the ROS domain from the environment, or None when absent."""

    env = os.environ if env is None else env
    raw = env.get("ROS_DOMAIN_ID")
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def sros2_marker_present(env: Mapping[str, str] | None = None) -> bool:
    """SROS2 is enabled, has a keystore and enforces access control."""

    env = os.environ if env is None else env
    enabled = str(env.get(SROS2_ENABLE_ENV, "")).strip().lower() in _TRUTHY
    keystore = bool(str(env.get(SROS2_KEYSTORE_ENV, "")).strip())
    strategy = str(env.get(SROS2_STRATEGY_ENV, "Enforce")).strip().lower()
    return enabled and keystore and strategy == "enforce"


def evaluate_dds_execute_gate(
    env: Mapping[str, str] | None = None,
    domain_id: int | None = None,
    topic: str = DDS_ACTION_TOPIC,
) -> DdsGateResult:
    """Evaluate the DDS execute gate. Deny is the default for every unknown."""

    env = os.environ if env is None else env
    domain = domain_id_from_env(env) if domain_id is None else domain_id
    sros2 = sros2_marker_present(env)

    raw_allow = env.get(ALLOW_ENV)
    if raw_allow is None:
        return DdsGateResult(
            False,
            DISABLED_BY_DEFAULT,
            f"{ALLOW_ENV} is not set; DDS ExecuteMotion is disabled by default",
            domain,
            topic,
            sros2,
        )
    if str(raw_allow).strip() != "1":
        return DdsGateResult(
            False,
            ENV_NOT_ONE,
            f"{ALLOW_ENV} must be exactly '1'",
            domain,
            topic,
            sros2,
        )
    if domain is None:
        return DdsGateResult(
            False,
            INVALID_DOMAIN,
            "ROS_DOMAIN_ID is missing or not an integer",
            None,
            topic,
            sros2,
        )
    if domain != PRIVILEGED_DOMAIN_ID:
        return DdsGateResult(
            False,
            WRONG_DOMAIN,
            f"domain {domain} is not the privileged domain {PRIVILEGED_DOMAIN_ID}",
            domain,
            topic,
            sros2,
        )
    if topic != DDS_ACTION_TOPIC:
        return DdsGateResult(
            False,
            WRONG_TOPIC,
            f"topic {topic!r} is not {DDS_ACTION_TOPIC!r}",
            domain,
            topic,
            sros2,
        )
    if not sros2:
        return DdsGateResult(
            False,
            SROS2_MISSING,
            "SROS2 is not enabled/enforced with a keystore",
            domain,
            topic,
            sros2,
        )
    return DdsGateResult(
        True,
        ALLOWED,
        "DDS ExecuteMotion gate passed",
        domain,
        topic,
        sros2,
    )


def dds_execute_allowed(
    env: Mapping[str, str] | None = None,
    domain_id: int | None = None,
    topic: str = DDS_ACTION_TOPIC,
) -> bool:
    return evaluate_dds_execute_gate(env, domain_id, topic).allowed
