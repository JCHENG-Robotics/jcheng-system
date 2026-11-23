"""In-memory/JSONL evidence store for the mock system."""

from __future__ import annotations

import json
from pathlib import Path
import uuid
from typing import Any

from .models import MotionCommand, MotionResult, ValidationReport
from .timebase import Clock


class EvidenceStore:
    def __init__(self, clock: Clock, path: str | Path | None = None) -> None:
        self.clock = clock
        self.path = Path(path) if path else None
        self.records: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def record_motion(
        self,
        command: MotionCommand,
        report: ValidationReport,
        result: MotionResult,
        operator: str = "mock",
    ) -> str:
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        record = {
            "schema_version": 1,
            "evidence_id": evidence_id,
            "kind": "motion",
            "created_at": self.clock.iso(self.clock.now()),
            "operator": operator,
            "contract_hash": command.contract.contract_hash,
            "profile_hash": result.safety.profile_hash,
            "request": command.to_dict(),
            "validation": report.to_dict(self.clock),
            "result": result.to_dict(self.clock),
            "observations": [
                {
                    "outcome": result.outcome,
                    "bus_verified": result.bus_verified,
                    "measured_positions": result.measured_positions,
                }
            ],
            "artifacts": [],
            "limitations": [],
        }
        self.records.append(record)
        self.events.append(
            {
                "event": "motion",
                "at": self.clock.iso(self.clock.now()),
                "evidence_id": evidence_id,
                "intent_id": command.intent_id,
                "outcome": result.outcome,
                "reason_code": result.reason_code,
            }
        )
        self._write(record)
        return evidence_id

    def record_event(self, event: str, **fields: Any) -> None:
        payload = {"event": event, "at": self.clock.iso(self.clock.now()), **fields}
        self.events.append(payload)
        self._write(payload)

    def page(self, after: int = 0, limit: int = 100) -> dict[str, Any]:
        items = self.events[after : after + limit]
        return {
            "items": items,
            "last_seq": after + len(items),
            "schema_version": 1,
        }
