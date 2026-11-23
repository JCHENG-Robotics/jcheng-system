"""Read-only telemetry, gateway, and preflight for M4."""

from .core import ReadOnlySafetyView, TelemetryCache, safety_state_dict
from .evidence import ReadOnlyEvidence, write_summary
from .gateway import ReadOnlyGatewayApp, ReadOnlyGatewayServer
from .preflight import (
    DomainConfig,
    PreflightInput,
    PreflightReport,
    ReadOnlyConfig,
    run_preflight,
)

__all__ = [
    "DomainConfig",
    "PreflightInput",
    "PreflightReport",
    "ReadOnlyConfig",
    "ReadOnlyEvidence",
    "ReadOnlyGatewayApp",
    "ReadOnlyGatewayServer",
    "ReadOnlySafetyView",
    "TelemetryCache",
    "run_preflight",
    "safety_state_dict",
    "write_summary",
]
