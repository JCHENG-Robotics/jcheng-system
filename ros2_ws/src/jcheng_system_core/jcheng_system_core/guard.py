"""Publisher-allowlist guard for the mock hardware bus."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import ErrorCode
from .safety import SafetySupervisor


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    violations: tuple[str, ...]


class PublisherGuard:
    def __init__(self, allowed: Iterable[str] = ("jcheng_hardware",)) -> None:
        self.allowed = set(allowed)

    def check(self, publishers: Iterable[str]) -> GuardResult:
        unexpected = sorted(set(publishers) - self.allowed)
        return GuardResult(
            ok=not unexpected,
            violations=tuple(f"unexpected hardware publisher: {name}" for name in unexpected),
        )

    def enforce(self, safety: SafetySupervisor, publishers: Iterable[str]) -> GuardResult:
        result = self.check(publishers)
        if not result.ok:
            safety.latch(ErrorCode.SAFETY_FAULT, "; ".join(result.violations))
        return result
