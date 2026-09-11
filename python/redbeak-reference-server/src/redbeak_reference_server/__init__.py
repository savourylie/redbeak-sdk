"""In-memory OpenAPI stand-in for runner tests and local checkpoints.

This is not the SaaS control plane and not a customer-facing capability.
It exists so the generic runner can be exercised without a network. Every
use goes through ``httpx.ASGITransport``; the process never listens.
"""

from __future__ import annotations

from redbeak_reference_server.http import create_app
from redbeak_reference_server.runtime import (
    FrozenClock,
    QueuedCase,
    ReferenceServer,
    SystemClock,
    WorkFile,
    load_work_file,
)

__all__ = [
    "FrozenClock",
    "QueuedCase",
    "ReferenceServer",
    "SystemClock",
    "WorkFile",
    "create_app",
    "load_work_file",
]
