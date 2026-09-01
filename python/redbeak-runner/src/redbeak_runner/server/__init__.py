"""In-memory reference implementation of the runner OpenAPI contract.

This is not the SaaS control plane. It exists so the generic runner can be
tested — and the first local checkpoint can run — without Supabase, auth UI, or
a network. TAI-162 replaces the key store; TAI-163 replaces the case store.
The protocol semantics stay here until those land.
"""

from __future__ import annotations

from redbeak_runner.server.http import create_app
from redbeak_runner.server.runtime import (
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
