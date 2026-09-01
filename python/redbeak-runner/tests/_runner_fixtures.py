"""Shared identifiers and a tiny adapter for runner tests.

No ``conftest.py``: the workspace already has one under the BFCL demo tests,
and mypy treats duplicate top-level test module names as a conflict.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import uuid4

from redbeak_adapter_sdk import (
    AdapterCapabilities,
    AgentOutput,
    Observation,
    ObservationRequest,
    SessionContext,
    SessionStore,
    UserInput,
)

T = TypeVar("T")

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
CASE_A = "33333333-3333-4333-8333-333333333333"
CASE_B = "55555555-5555-4555-8555-555555555555"
SCENARIO_A = "44444444-4444-4444-8444-444444444444"
SCENARIO_B = "66666666-6666-4666-8666-666666666666"
CREATED_AT = "2026-09-01T10:15:30Z"


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def uid() -> str:
    return str(uuid4())


class NoteAdapter:
    """One-string-per-session target used to exercise the protocol."""

    def __init__(self) -> None:
        self.sessions: SessionStore[dict[str, str]] = SessionStore()
        self.sent: list[str] = []

    async def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="note",
            adapter_version="0.1.0",
            observable_fact_names=("note_state",),
        )

    async def reset(self, context: SessionContext) -> None:
        self.sessions.open(context.session_id, {"note": str(context.setup.get("note", ""))})

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        state = self.sessions.get(context.session_id)
        state["note"] = input.content
        self.sent.append(input.content)
        return AgentOutput(content=f"noted: {input.content}")

    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]:
        if "note_state" not in request.fact_names:
            return []
        return [
            Observation(
                name="note_state",
                value=self.sessions.get(context.session_id)["note"],
                source="customer_state",
            )
        ]

    async def close(self, context: SessionContext) -> None:
        self.sessions.close(context.session_id)


def two_turn_case(
    *,
    case_execution_id: str = CASE_A,
    scenario_id: str = SCENARIO_A,
    project_id: str = PROJECT_ID,
    run_id: str = RUN_ID,
    first: str = "hello",
    second: str = "again",
    external_id: str = "note_case",
) -> Any:
    from redbeak_runner.server import QueuedCase

    return QueuedCase(
        project_id=project_id,
        run_id=run_id,
        case_execution_id=case_execution_id,
        scenario_id=scenario_id,
        scenario_external_id=external_id,
        setup={"type": "inline", "payload": {"note": "seed"}},
        turns=(
            {"sequence": 0, "type": "user_message", "content": first},
            {"sequence": 1, "type": "user_message", "content": second},
        ),
        observation_request={"fact_names": ["note_state"]},
        target_version="0.1.0",
    )


def frozen_now() -> datetime:
    return datetime(2026, 9, 1, 10, 15, 30, tzinfo=UTC)
