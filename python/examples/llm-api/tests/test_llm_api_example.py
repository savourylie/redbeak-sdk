"""The example adapter, exercised against a stubbed provider.

Nothing here reaches a network, a model, or a Redbeak account, and no test needs
a credential: the provider is an ``httpx.MockTransport`` that answers from a
script. What the tests pin is the boundary — what leaves for the provider, what
arrives at Redbeak, and which failures are the model's rather than ours.

``redbeak_runner`` is imported by one test to prove the adapter is discoverable
by the shipped CLI. It is present because this repository's workspace installs
every package; the example itself does not depend on the runner at runtime.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from typing import Any, TypeVar

import httpx
import pytest
from redbeak_adapter_sdk import (
    AdapterConfigurationError,
    AdapterError,
    AdapterInternalError,
    AdapterProtocolError,
    AdapterTimeoutError,
    AgentOutput,
    ObservationRequest,
    SessionContext,
    TargetAdapter,
    TargetUnavailableError,
    UserInput,
    is_target_failure,
    normalize_exception,
)
from redbeak_example_llm_api import LlmApiAdapter, create_adapter

T = TypeVar("T")

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"
CASE_EXECUTION_ID = "33333333-3333-4333-8333-333333333333"
OTHER_CASE_EXECUTION_ID = "55555555-5555-4555-8555-555555555555"
SCENARIO_ID = "44444444-4444-4444-8444-444444444444"

API_KEY = "sk-test-secret-value"
BASE_URL = "https://models.example.test/v1"
MODEL = "example-model-1"


def run(coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def context(case_execution_id: str = CASE_EXECUTION_ID) -> SessionContext:
    return SessionContext(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        case_execution_id=case_execution_id,
        scenario_id=SCENARIO_ID,
        scenario_external_id="tmmlu_sample_1",
    )


def completion(content: Any) -> dict[str, Any]:
    """A chat completion shaped the way an OpenAI-compatible provider sends it."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
    }


class Provider:
    """A scripted model endpoint that records exactly what it was sent."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("the adapter made more provider calls than the test scripted")
        return self._responses.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        return dict(json.loads(self.requests[index].content))


def adapter_for(provider: Provider) -> LlmApiAdapter:
    return LlmApiAdapter(
        base_url=BASE_URL,
        api_key=API_KEY,
        model=MODEL,
        transport=provider.transport(),
    )


def ask(
    adapter: LlmApiAdapter,
    text: str,
    *,
    sequence: int = 0,
    case: str = CASE_EXECUTION_ID,
) -> AgentOutput:
    return run(adapter.send(UserInput(sequence=sequence, content=text), context(case)))


# --------------------------------------------------------------------------
# Discovery and lifecycle
# --------------------------------------------------------------------------


def test_the_shipped_runner_can_discover_the_example(monkeypatch: pytest.MonkeyPatch) -> None:
    from redbeak_runner.adapters import load_adapter

    monkeypatch.setenv("LLM_API_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_API_KEY", API_KEY)
    monkeypatch.setenv("LLM_API_MODEL", MODEL)

    # The spec a customer passes to --adapter, resolved by the real loader.
    adapter = load_adapter("redbeak_example_llm_api")

    assert isinstance(adapter, TargetAdapter)
    assert run(adapter.capabilities()).adapter_name == "llm_api"


def test_missing_provider_configuration_is_refused_before_any_work_is_claimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_BASE_URL", BASE_URL)
    monkeypatch.setenv("LLM_API_MODEL", MODEL)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(AdapterConfigurationError) as raised:
        create_adapter()

    assert "LLM_API_KEY" in str(raised.value)


def test_the_lifecycle_needs_no_session_state() -> None:
    provider = Provider(httpx.Response(200, json=completion("A")))
    adapter = adapter_for(provider)
    session = context()

    run(adapter.reset(session))
    run(adapter.reset(session))
    assert ask(adapter, "Q?").content == "A"
    run(adapter.close(session))
    run(adapter.close(session))


def test_no_observations_are_reported() -> None:
    adapter = adapter_for(Provider())
    request = ObservationRequest(fact_names=("order_state",))

    observations = run(adapter.observe(request, context()))

    assert observations == []
    assert run(adapter.capabilities()).supports_observations is False


# --------------------------------------------------------------------------
# What crosses the boundary
# --------------------------------------------------------------------------


def test_the_provider_receives_the_question_and_nothing_about_redbeak() -> None:
    provider = Provider(httpx.Response(200, json=completion("(B)")))
    adapter = adapter_for(provider)

    ask(adapter, "下列何者為質數？ (A) 4 (B) 7")

    request = provider.requests[0]
    assert str(request.url) == f"{BASE_URL}/chat/completions"
    assert provider.body() == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "下列何者為質數？ (A) 4 (B) 7"}],
    }
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    sent = request.content.decode() + str(request.url) + str(dict(request.headers))
    for identifier in (PROJECT_ID, RUN_ID, CASE_EXECUTION_ID, SCENARIO_ID, "tmmlu_sample_1"):
        assert identifier not in sent


def test_redbeak_receives_the_reply_unchanged() -> None:
    reply = "  正確答案是 (B) 7。\n\n理由：7 只有 1 和自己兩個因數。  "
    provider = Provider(httpx.Response(200, json=completion(reply)))

    output = ask(adapter_for(provider), "下列何者為質數？")

    # Verbatim: no trimming, no option extraction, no scoring. Whatever the
    # benchmark needs is parsed in Redbeak from this preserved text.
    assert output.content == reply
    assert output.to_dict() == {"type": "agent_message", "content": reply}
    assert output.artifacts == ()


def test_an_empty_reply_is_an_answer_rather_than_a_failure() -> None:
    provider = Provider(
        httpx.Response(200, json=completion("")),
        httpx.Response(200, json=completion(None)),
    )
    adapter = adapter_for(provider)

    assert ask(adapter, "Q1?").content == ""
    assert ask(adapter, "Q2?").content == ""


def test_each_case_is_an_independent_request_with_no_history() -> None:
    provider = Provider(
        httpx.Response(200, json=completion("first")),
        httpx.Response(200, json=completion("second")),
    )
    adapter = adapter_for(provider)

    first = ask(adapter, "Question one?")
    second = ask(adapter, "Question two?", case=OTHER_CASE_EXECUTION_ID)

    assert (first.content, second.content) == ("first", "second")
    for index, text in enumerate(("Question one?", "Question two?")):
        messages = provider.body(index)["messages"]
        assert messages == [{"role": "user", "content": text}]
    # The second call carries no trace of the first case.
    assert "Question one?" not in provider.requests[1].content.decode()
    assert "first" not in provider.requests[1].content.decode()


def test_a_second_turn_within_one_case_never_reaches_the_model() -> None:
    provider = Provider(httpx.Response(200, json=completion("first")))
    adapter = adapter_for(provider)

    ask(adapter, "Question one?")
    with pytest.raises(AdapterConfigurationError):
        ask(adapter, "And the follow-up?", sequence=1)

    assert len(provider.requests) == 1


# --------------------------------------------------------------------------
# Whose failure is it
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected", "stage"),
    [
        (500, TargetUnavailableError, "target_unavailable"),
        (503, TargetUnavailableError, "target_unavailable"),
        (429, TargetUnavailableError, "target_unavailable"),
        (401, AdapterConfigurationError, "adapter"),
        (403, AdapterConfigurationError, "adapter"),
        (400, AdapterInternalError, "adapter"),
    ],
)
def test_provider_http_failures_are_attributed_to_the_right_side(
    status: int, expected: type[Exception], stage: str
) -> None:
    provider = Provider(httpx.Response(status, json={"error": {"message": "upstream detail"}}))

    with pytest.raises(expected) as raised:
        ask(adapter_for(provider), "Q?")

    error = normalize_exception(raised.value)
    assert error.stage == stage
    assert is_target_failure(error) == (stage == "target_unavailable")


def test_an_unreachable_provider_is_a_target_failure() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    adapter = LlmApiAdapter(
        base_url=BASE_URL, api_key=API_KEY, model=MODEL, transport=httpx.MockTransport(refuse)
    )

    with pytest.raises(TargetUnavailableError) as raised:
        ask(adapter, "Q?")

    assert normalize_exception(raised.value).stage == "target_unavailable"


def test_a_slow_provider_times_out_as_a_timeout() -> None:
    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    adapter = LlmApiAdapter(
        base_url=BASE_URL, api_key=API_KEY, model=MODEL, transport=httpx.MockTransport(stall)
    )

    with pytest.raises(AdapterTimeoutError) as raised:
        ask(adapter, "Q?")

    error = normalize_exception(raised.value)
    assert error.stage == "timeout"
    assert error.retryable is True


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"unexpected": True},
        "not an object",
    ],
)
def test_an_unreadable_reply_is_our_problem_not_the_models(payload: Any) -> None:
    provider = Provider(httpx.Response(200, json=payload))

    with pytest.raises(AdapterProtocolError) as raised:
        ask(adapter_for(provider), "Q?")

    # "protocol", never "target_unavailable": we could not read the answer, which
    # is not evidence that the model failed to give one.
    assert normalize_exception(raised.value).stage == "protocol"


def test_a_non_text_reply_is_refused() -> None:
    provider = Provider(httpx.Response(200, json=completion([{"type": "text", "text": "hi"}])))

    with pytest.raises(AdapterProtocolError):
        ask(adapter_for(provider), "Q?")


def test_no_failure_message_carries_the_credential_or_the_question() -> None:
    question = "下列何者為質數？ (A) 4 (B) 7"
    leaky = {"error": {"message": f"key {API_KEY} cannot use model for prompt {question!r}"}}
    failures = [
        Provider(httpx.Response(500, json=leaky)),
        Provider(httpx.Response(401, json=leaky)),
        Provider(httpx.Response(400, json=leaky)),
        Provider(httpx.Response(200, json={"choices": []})),
    ]

    for provider in failures:
        with pytest.raises(AdapterError) as raised:
            ask(adapter_for(provider), question)
        message = normalize_exception(raised.value).message
        assert API_KEY not in message
        assert question not in message
        assert "upstream" not in message and "cannot use model" not in message
