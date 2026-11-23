"""JSONL evidence for a read-only telemetry run."""

from __future__ import annotations

import json
from pathlib import Path
import threading
from typing import Any

from jcheng_system_core.models import RobotState


class ReadOnlyEvidence:
    def __init__(self, clock: Any, path: str | Path | None = None) -> None:
        self.clock = clock
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.samples: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self._last_sample_at: float | None = None
        self._max_gap_s = 0.0
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def _write(self, record: dict[str, Any]) -> None:
        if self.path:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def record_sample(self, state: RobotState, health: dict[str, Any], **extra: Any) -> None:
        with self._lock:
            stamp = float(state.stamp)
            if self.started_at is None:
                self.started_at = stamp
            if self._last_sample_at is not None:
                self._max_gap_s = max(self._max_gap_s, max(0.0, stamp - self._last_sample_at))
            self._last_sample_at = stamp
            self.ended_at = stamp
            record = {
                "schema_version": 1,
                "kind": "readonly_sample",
                "at": self.clock.iso(stamp),
                "monotonic": stamp,
                "state": state.to_dict(self.clock),
                "health": health,
                "writes": 0,
                **extra,
            }
            self.samples.append(record)
            self._write(record)

    def record_event(self, event: str, **fields: Any) -> None:
        with self._lock:
            record = {"kind": "readonly_event", "event": event, **fields}
            self.events.append(record)
            self._write(record)

    def page(self, after: int = 0, limit: int = 100) -> dict[str, Any]:
        with self._lock:
            items = self.events[after : after + limit]
            return {"items": items, "last_seq": after + len(items), "schema_version": 1}

    def summary(self, require_bus: bool = True, max_gap_s: float = 2.0) -> dict[str, Any]:
        with self._lock:
            truth_counts: dict[str, int] = {}
            for sample in self.samples:
                truth = str(sample["state"].get("truth", "unknown"))
                truth_counts[truth] = truth_counts.get(truth, 0) + 1
            measured = truth_counts.get("measured", 0)
            duration = 0.0
            if self.started_at is not None and self.ended_at is not None:
                duration = max(0.0, self.ended_at - self.started_at)
            ok = (
                bool(self.samples)
                and self._max_gap_s <= max_gap_s
                and (measured > 0 if require_bus else True)
            )
            return {
                "schema_version": 1,
                "kind": "readonly_run",
                "ok": bool(ok),
                "mode": "readonly",
                "writes": 0,
                "require_bus": bool(require_bus),
                "samples": len(self.samples),
                "measured_samples": measured,
                "commanded_samples": truth_counts.get("commanded", 0),
                "truth_counts": truth_counts,
                "duration_s": duration,
                "max_gap_s": self._max_gap_s,
                "started_at": self.clock.iso(self.started_at) if self.started_at is not None else None,
                "ended_at": self.clock.iso(self.ended_at) if self.ended_at is not None else None,
                "events": len(self.events),
            }


def write_summary(path: str | Path, summary: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
