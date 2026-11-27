"""Mock streaming bridge: one atomic multi-joint frame per intake step (M6).

The real vendor path packs all servo targets into one `ServosPosition` message
(see `docs/proposals/STREAMING-EXECUTOR.md`). This mock records exactly that
shape so the intake/arbiter streaming behaviour can be tested without hardware:
one frame per `JOINT_STEP` carrying every joint, never a serialized per-joint
sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import MotionCommand
from .mock_hardware import MockHardwareBridge


@dataclass(frozen=True)
class StreamingFrame:
    at: float
    names: tuple[str, ...]
    targets: tuple[float, ...]
    duration_s: float

    def to_dict(self) -> dict:
        return {
            "at": self.at,
            "names": list(self.names),
            "targets": list(self.targets),
            "duration_s": self.duration_s,
        }


class MockStreamingBridge(MockHardwareBridge):
    """`MockHardwareBridge` plus an atomic-frame log for multi-joint steps."""

    def __init__(self, profile, clock) -> None:
        super().__init__(profile, clock)
        self.frames: list[StreamingFrame] = []

    def publish(self, command: MotionCommand, now: float | None = None) -> None:
        super().publish(command, now)
        if command.kind not in {"JOINT_STEP", "HOME"}:
            return
        names = tuple(command.joint_names or self.profile.joint_names)
        targets = tuple(command.target_positions or [0.0] * len(names))
        if len(names) <= 1:
            return
        at = self.clock.now() if now is None else float(now)
        self.frames.append(
            StreamingFrame(
                at=at,
                names=names,
                targets=targets,
                duration_s=float(command.duration_s or 0.0),
            )
        )
