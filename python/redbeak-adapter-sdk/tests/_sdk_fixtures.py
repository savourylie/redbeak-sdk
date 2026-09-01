"""Shared helpers and identifiers for the adapter-SDK tests.

There is no ``pytest-asyncio`` here on purpose. The SDK's whole async surface is
five methods, every test drives one call to completion, and ``asyncio.run`` does
that in one line. Adding a plugin — and a lockfile entry, and a marker
convention — to save that line would be a poor trade in a package whose selling
point is that it needs almost nothing installed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
CASE_EXECUTION_ID = "33333333-3333-4333-8333-333333333333"
OTHER_CASE_EXECUTION_ID = "55555555-5555-4555-8555-555555555555"
SCENARIO_ID = "44444444-4444-4444-8444-444444444444"


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    """Drive one coroutine to completion."""
    return asyncio.run(coroutine)


def assignment() -> dict[str, Any]:
    """A fresh work-claim assignment, shaped exactly as the contract defines it.

    A plain function rather than a pytest fixture in a ``conftest.py``: this
    package and the BFCL demo would then own two top-level modules both named
    ``conftest``, which mypy refuses to resolve.
    """
    return {
        "project_id": PROJECT_ID,
        "run_id": RUN_ID,
        "case_execution_id": CASE_EXECUTION_ID,
        "scenario_id": SCENARIO_ID,
        "scenario_external_id": "multi_turn_base_3",
        "target_version": "0.1.0",
        "sequence": 0,
        "setup": {"type": "inline", "payload": {"GorillaFileSystem": {"root": {}}}},
        "input": {"sequence": 0, "type": "user_message", "content": "find the test files"},
        "observation_request": {"fact_names": ["gorilla_file_system_state"]},
    }
