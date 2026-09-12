"""The single-turn base: what it supplies, and the limit it enforces.

The base exists so a stateless integration is one method long. These tests pin
both halves of that bargain — the four methods a subclass inherits behave as the
contract requires, and the one method it writes cannot quietly be used for a
conversation the adapter has no way to hold.
"""

from __future__ import annotations

import pytest
from _sdk_fixtures import (
    CASE_EXECUTION_ID,
    OTHER_CASE_EXECUTION_ID,
    PROJECT_ID,
    RUN_ID,
    SCENARIO_ID,
    run,
)
from redbeak_adapter_sdk import (
    AdapterConfigurationError,
    AgentOutput,
    ObservationRequest,
    SessionContext,
    SingleTurnTextAdapter,
    TargetAdapter,
    UserInput,
    is_target_failure,
    normalize_exception,
)


class EchoOnce(SingleTurnTextAdapter):
    """The smallest real subclass: one method, no state."""

    def __init__(self) -> None:
        super().__init__(adapter_name="echo_once", adapter_version="0.1.0")
        self.questions: list[str] = []

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        question = self.require_single_turn(input)
        self.questions.append(question)
        return AgentOutput(content=f"answer to {question}")


def context(case_execution_id: str = CASE_EXECUTION_ID) -> SessionContext:
    return SessionContext(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        case_execution_id=case_execution_id,
        scenario_id=SCENARIO_ID,
        scenario_external_id="tmmlu_sample_1",
    )


def question(text: str, sequence: int = 0) -> UserInput:
    return UserInput(sequence=sequence, content=text)


def test_a_subclass_satisfies_the_five_method_protocol() -> None:
    # The base is a convenience, not a second protocol: a runner loading this
    # adapter must not be able to tell it apart from a hand-written one.
    assert isinstance(EchoOnce(), TargetAdapter)


def test_send_is_the_only_method_left_to_write() -> None:
    class Incomplete(SingleTurnTextAdapter):
        pass

    with pytest.raises(TypeError):
        Incomplete(adapter_name="incomplete", adapter_version="0.1.0")  # type: ignore[abstract]


def test_capabilities_declare_a_target_with_no_observable_state() -> None:
    capabilities = run(EchoOnce().capabilities())

    assert capabilities.adapter_name == "echo_once"
    assert capabilities.supports_observations is False
    assert capabilities.supports_artifacts is False
    assert capabilities.observable_fact_names == ()
    # Nothing about the integration is announced to the server beyond the
    # contract's own claim fields.
    assert set(capabilities.to_work_claim_capabilities()) == {
        "max_concurrent_cases",
        "supports_observations",
        "supports_artifacts",
    }


def test_a_refused_name_fails_at_construction_not_mid_run() -> None:
    class Named(SingleTurnTextAdapter):
        async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
            return AgentOutput(content="")

    with pytest.raises(AdapterConfigurationError):
        Named(adapter_name="Echo Once", adapter_version="0.1.0")


def test_reset_and_close_are_idempotent_and_need_no_session() -> None:
    adapter = EchoOnce()
    session = context()

    run(adapter.reset(session))
    run(adapter.reset(session))
    assert run(adapter.send(question("2 + 2?"), session)).content == "answer to 2 + 2?"
    run(adapter.close(session))
    run(adapter.close(session))


def test_observe_reports_an_evidence_gap_rather_than_inventing_a_fact() -> None:
    adapter = EchoOnce()
    request = ObservationRequest(fact_names=("order_state", "refund_state"))

    observations = run(adapter.observe(request, context()))

    assert observations == []
    # The evaluator sees precisely which facts went unanswered.
    assert request.missing_from(observations) == ("order_state", "refund_state")


def test_cases_share_nothing() -> None:
    adapter = EchoOnce()

    first = run(adapter.send(question("who wrote it?"), context()))
    second = run(adapter.send(question("when?"), context(OTHER_CASE_EXECUTION_ID)))

    assert first.content == "answer to who wrote it?"
    assert second.content == "answer to when?"
    assert adapter.questions == ["who wrote it?", "when?"]


def test_a_second_turn_is_refused_and_blamed_on_the_integration() -> None:
    adapter = EchoOnce()

    with pytest.raises(AdapterConfigurationError) as raised:
        run(adapter.send(question("and the year?", sequence=1), context()))

    error = normalize_exception(raised.value)
    assert error.stage == "adapter"
    assert not is_target_failure(error)
    # The unanswerable turn never reached the target.
    assert adapter.questions == []
