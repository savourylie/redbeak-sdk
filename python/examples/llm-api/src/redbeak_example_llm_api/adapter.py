"""A Redbeak target adapter for a plain LLM API. One question, one answer.

The system under test here is the model endpoint itself: no tools, no business
state, no conversation. That makes this the shortest integration Redbeak
supports, and the one worth reading first — everything below the ``send``
method is the HTTP call any client would make, and everything a Redbeak
integration adds is in ``send`` itself.

Two recipients, and they receive different things:

* **The model provider** receives the question text and the credential that
  authorises that call, and nothing else. Not the Redbeak project, run, or case
  identifiers; not the scenario name.
* **Redbeak** receives the model's user-visible reply, unchanged, and the
  protocol metadata the runner adds around it. No observations, because an API
  that answers questions changes no business state. No provider credential, and
  no provider error body — a provider's error text can quote the request back,
  so only the status code travels.

The endpoint is the OpenAI-compatible ``POST /chat/completions``, which is spoken
by OpenAI and by most hosted and self-hosted providers, so pointing
``LLM_API_BASE_URL`` elsewhere is usually the whole of the change needed to run
this against another model.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from redbeak_adapter_sdk import (
    AdapterConfigurationError,
    AdapterInternalError,
    AdapterProtocolError,
    AdapterTimeoutError,
    AgentOutput,
    SessionContext,
    SingleTurnTextAdapter,
    TargetUnavailableError,
    UserInput,
)

#: This example's own version, reported to Redbeak as the adapter version. It
#: identifies the integration code, not the model: which model answered is
#: recorded by the TargetVersion the Run was created with.
ADAPTER_VERSION = "0.1.0"

#: Long enough for a slow model turn, short enough that a hung endpoint fails
#: the case instead of holding the lease until it expires.
DEFAULT_TIMEOUT_S = 60.0


class LlmApiAdapter(SingleTurnTextAdapter):
    """Answers each case by asking an OpenAI-compatible chat endpoint once.

    The base class supplies ``capabilities``, ``reset``, ``observe`` and
    ``close``; everything they do is dictated by the target being stateless, so
    this class implements ``send`` and nothing else.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(adapter_name="llm_api", adapter_version=ADAPTER_VERSION)
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        # Tests pass a stub transport so the suite never reaches a provider or
        # needs a key. Production leaves it None and httpx connects normally.
        self._transport = transport

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        """Ask the model one question and hand Redbeak its answer, unchanged."""
        question = self.require_single_turn(input)
        reply = await self._ask_model(question)
        return AgentOutput(content=reply)

    async def _ask_model(self, question: str) -> str:
        """One request, one connection, nothing carried to the next case.

        This method and the two below it are the only provider-specific code
        here. Pointing at another OpenAI-compatible endpoint needs no change
        at all; a provider with a different request shape needs these three
        and nothing else.
        """
        body = {"model": self._model, "messages": [{"role": "user", "content": question}]}
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                transport=self._transport,
            ) as http:
                response = await http.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AdapterTimeoutError("the model provider did not answer in time") from exc
        except httpx.HTTPError as exc:
            # The exception's own text can contain the full URL; only its type
            # is safe to report.
            raise TargetUnavailableError(
                f"could not reach the model provider ({type(exc).__name__})"
            ) from exc
        _raise_for_status(response.status_code)
        return _reply_text(response)


def _raise_for_status(status_code: int) -> None:
    """Classify a provider HTTP failure by whose problem it is.

    The distinction is the one the architecture is built on: a wrong answer is a
    result, while an unreachable provider, a rejected key, and a malformed
    request are three different faults, none of which should be counted as the
    model answering badly.

    Only the status code travels. A provider's error body routinely quotes the
    request back, and sometimes the key, so it is never put into evidence.
    """
    if status_code < 400:
        return
    if status_code in (401, 403):
        raise AdapterConfigurationError(
            f"the model provider rejected the credential (HTTP {status_code}); check LLM_API_KEY"
        )
    if status_code == 429 or status_code >= 500:
        raise TargetUnavailableError(
            f"the model provider could not serve the request (HTTP {status_code})"
        )
    raise AdapterInternalError(f"the model provider refused the request (HTTP {status_code})")


def _reply_text(response: httpx.Response) -> str:
    """The user-visible text, exactly as the model wrote it.

    No trimming, no extraction, no normalisation. Whatever parsing a benchmark
    needs happens in Redbeak against the preserved raw reply; doing any of it
    here would put the client between the model and its own evidence.
    """
    try:
        content: Any = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise AdapterProtocolError(
            f"the model provider's reply was not OpenAI-compatible ({type(exc).__name__})"
        ) from exc
    if content is None:
        # A model that says nothing has still answered. An empty answer is a
        # completed turn for the evaluator to judge, not an integration failure.
        return ""
    if not isinstance(content, str):
        raise AdapterProtocolError("the model provider returned a reply that was not text")
    return content


def create_adapter() -> LlmApiAdapter:
    """Build the adapter from the environment. Discovered by ``--adapter``.

    Configuration is refused here rather than per case: a missing key should stop
    the run before it claims work, not fail fifty cases identically.
    """
    return LlmApiAdapter(
        base_url=_required("LLM_API_BASE_URL"),
        api_key=_required("LLM_API_KEY"),
        model=_required("LLM_API_MODEL"),
        timeout_s=float(os.environ.get("LLM_API_TIMEOUT_S", DEFAULT_TIMEOUT_S)),
    )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AdapterConfigurationError(f"set {name} to configure the model provider")
    return value


__all__ = ["ADAPTER_VERSION", "DEFAULT_TIMEOUT_S", "LlmApiAdapter", "create_adapter"]
