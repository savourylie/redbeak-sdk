"""The five-method contract, exercised through a minimal adapter.

The adapter here is as small as one can be and still be real: it keeps a
per-session note, answers with it, and observes it as a fact. That is enough to
pin the lifecycle, the return types, and the two rules the signatures cannot
express — an unrecognised failure is attributed to the adapter, and a bad answer
is still an answer.
"""

from __future__ import annotations

import pytest
from _sdk_fixtures import CASE_EXECUTION_ID, PROJECT_ID, RUN_ID, SCENARIO_ID, run
from redbeak_adapter_sdk import (
    AdapterCapabilities,
    AgentOutput,
    Observation,
    ObservationRequest,
    SessionContext,
    SessionStore,
    TargetAdapter,
    TargetUnavailableError,
    UnknownSessionError,
    UserInput,
    is_target_failure,
    normalize_exception,
)

FACT = "note_state"


class NoteAdapter:
    """A target that remembers one string per session."""

    def __init__(self, *, crash: bool = False, silent: bool = False) -> None:
        self.sessions: SessionStore[dict[str, str]] = SessionStore()
        self.crash = crash
        self.silent = silent

    async def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="note",
            adapter_version="0.1.0",
            observable_fact_names=(FACT,),
        )

    async def reset(self, context: SessionContext) -> None:
        self.sessions.open(context.session_id, {"note": str(context.setup.get("note", ""))})

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        if self.crash:
            raise TargetUnavailableError("the target process is gone")
        state = self.sessions.get(context.session_id)
        state["note"] = input.content
        return AgentOutput(content="" if self.silent else f"noted: {input.content}")

    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]:
        state = self.sessions.get(context.session_id)
        if FACT not in request.fact_names:
            return []
        return [Observation(name=FACT, value=state["note"], source="customer_state")]

    async def close(self, context: SessionContext) -> None:
        self.sessions.close(context.session_id)


def context(setup: dict[str, str] | None = None) -> SessionContext:
    return SessionContext(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        case_execution_id=CASE_EXECUTION_ID,
        scenario_id=SCENARIO_ID,
        scenario_external_id="note_case",
        setup=setup or {},
    )


def test_a_plain_class_satisfies_the_protocol() -> None:
    """Structural, not nominal: a customer must not have to import a base class."""
    adapter: TargetAdapter = NoteAdapter()
    assert isinstance(adapter, TargetAdapter)


def test_the_lifecycle_runs_end_to_end() -> None:
    adapter = NoteAdapter()
    session = context({"note": "start"})

    async def scenario() -> tuple[AgentOutput, list[Observation]]:
        await adapter.reset(session)
        output = await adapter.send(UserInput(sequence=0, content="unlock the doors"), session)
        observations = await adapter.observe(ObservationRequest(fact_names=(FACT,)), session)
        await adapter.close(session)
        return output, observations

    output, observations = run(scenario())
    assert output.content == "noted: unlock the doors"
    assert [observation.to_dict() for observation in observations] == [
        {"name": FACT, "value": "unlock the doors", "source": "customer_state"}
    ]


def test_capabilities_answers_without_an_open_session() -> None:
    """A runner asks before it claims work, so this cannot depend on a session."""
    assert run(NoteAdapter().capabilities()).observable_fact_names == (FACT,)


def test_reset_is_idempotent() -> None:
    adapter = NoteAdapter()
    session = context({"note": "start"})

    async def scenario() -> list[Observation]:
        await adapter.reset(session)
        await adapter.send(UserInput(sequence=0, content="changed"), session)
        await adapter.reset(session)
        return await adapter.observe(ObservationRequest(fact_names=(FACT,)), session)

    assert run(scenario())[0].value == "start"


def test_close_is_safe_to_call_twice() -> None:
    adapter = NoteAdapter()
    session = context()

    async def scenario() -> None:
        await adapter.reset(session)
        await adapter.close(session)
        await adapter.close(session)

    run(scenario())


def test_using_a_closed_session_is_an_adapter_failure_not_a_target_failure() -> None:
    adapter = NoteAdapter()
    session = context()

    async def scenario() -> None:
        await adapter.send(UserInput(sequence=0, content="hello"), session)

    with pytest.raises(UnknownSessionError) as caught:
        run(scenario())
    assert not is_target_failure(caught.value)
    assert normalize_exception(caught.value).stage == "adapter"


def test_a_crashed_target_is_attributed_to_the_target() -> None:
    adapter = NoteAdapter(crash=True)
    session = context()

    async def scenario() -> None:
        await adapter.reset(session)
        await adapter.send(UserInput(sequence=0, content="hello"), session)

    with pytest.raises(TargetUnavailableError) as caught:
        run(scenario())
    assert is_target_failure(caught.value)


def test_a_silent_answer_is_a_completed_turn_not_a_failure() -> None:
    """Answer quality is the evaluator's business, never the adapter's."""
    adapter = NoteAdapter(silent=True)
    session = context()

    async def scenario() -> AgentOutput:
        await adapter.reset(session)
        return await adapter.send(UserInput(sequence=0, content="hello"), session)

    assert run(scenario()).content == ""


def test_an_unobservable_fact_is_omitted_rather_than_raised() -> None:
    adapter = NoteAdapter()
    session = context()
    request = ObservationRequest(fact_names=("something_else_state",))

    async def scenario() -> list[Observation]:
        await adapter.reset(session)
        return await adapter.observe(request, session)

    observations = run(scenario())
    assert observations == []
    assert request.missing_from(observations) == ("something_else_state",)
