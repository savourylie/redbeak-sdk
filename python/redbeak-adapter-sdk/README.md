# `redbeak-adapter-sdk`

The customer-side half of the Redbeak boundary. A target integration implements
one asynchronous protocol; everything else in this package exists to make the
data boundary hold without the integrator having to think about it.

Nothing here reaches the network or requires a credential.

## The contract

```python
class TargetAdapter(Protocol):
    async def capabilities(self) -> AdapterCapabilities: ...
    async def reset(self, context: SessionContext) -> None: ...
    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput: ...
    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]: ...
    async def close(self, context: SessionContext) -> None: ...
```

It is a `Protocol`, so an adapter satisfies it structurally: no base class to
inherit, nothing to register. The lifecycle for one case execution is `reset()`
once, then `send()` and `observe()` per turn, then `close()`.

What the target does internally — how many model calls, which tools, which
framework — is invisible by design. Redbeak requires only the user-visible
output and the facts it asked to observe.

## Three rules the signatures cannot express

**`observe()` returns facts, never a verdict.** An observation carries what was
seen. Whether that satisfies a hidden expected outcome is decided by the
evaluator, in Redbeak Cloud. An adapter that returned a pass/fail would have
made the customer's own code the scorer, which is exactly what a black-box
evaluation must not allow. `Observation` enforces this: the contract schema
refuses unknown top-level properties, and the SDK also walks the observed value
and rejects verdict-shaped keys nested inside it.

**A target that answers badly has still answered.** A weak, wrong, or empty
answer is a normal completed turn whose quality the evaluator judges. Raise
`TargetUnavailableError` only when the system under test could not be reached or
did not survive the call. Every other failure normalises to an adapter-stage
error, reported separately from target results — including an exception this
SDK does not recognise, which is attributed to Redbeak rather than to the
customer.

**`reset()` must be deterministic and idempotent.** The same context must
produce the same starting state every time, including on a second call for a
session that is already open. A retried case has to be judged against the same
world as the first attempt. `SessionStore` gives you this: it stores a deep copy
per session id and replaces rather than merges.

## What the SDK refuses

Every input an adapter receives — the session context and its setup payload, the
user input, the observation request — validates itself on construction against
contract `0.1` and against the evaluation-private blocklist read out of that
contract. Ground truth, expected outcomes, rubrics, scoring thresholds, and
future turns cannot reach adapter code, and a payload that carries one is
refused loudly rather than quietly stripped.

Matching is by exact property name. BFCL's own mock state contains `password`,
`passenger`, and `expiry_date`; a substring rule would reject legitimate domain
data while adding no protection a name list lacks.

There is also no field for a future turn. `UserInput` is one turn, not a list,
so "the adapter never sees what comes next" is a property of the type rather
than a rule to remember.

## A minimal adapter

```python
from redbeak_adapter_sdk import (
    AdapterCapabilities,
    AgentOutput,
    Observation,
    ObservationRequest,
    SessionContext,
    SessionStore,
    UserInput,
)


class EchoAdapter:
    def __init__(self) -> None:
        self.sessions: SessionStore[dict[str, str]] = SessionStore()

    async def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_name="echo",
            adapter_version="0.1.0",
            observable_fact_names=("last_message_state",),
        )

    async def reset(self, context: SessionContext) -> None:
        self.sessions.open(context.session_id, {"last": ""})

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        self.sessions.get(context.session_id)["last"] = input.content
        return AgentOutput(content=f"you said: {input.content}")

    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]:
        if "last_message_state" not in request.fact_names:
            return []
        return [
            Observation(
                name="last_message_state",
                value=self.sessions.get(context.session_id)["last"],
                source="customer_state",
            )
        ]

    async def close(self, context: SessionContext) -> None:
        self.sessions.close(context.session_id)
```

## When the target is stateless: one method

Four of the five methods are identical for every target that answers text and
holds nothing — a plain LLM API being the common case. `SingleTurnTextAdapter`
supplies those four, so that integration is one method long:

```python
from redbeak_adapter_sdk import AgentOutput, SessionContext, SingleTurnTextAdapter, UserInput


class MyApiAdapter(SingleTurnTextAdapter):
    def __init__(self) -> None:
        super().__init__(adapter_name="my_api", adapter_version="0.1.0")

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        question = self.require_single_turn(input)
        return AgentOutput(content=await my_model(question))
```

It is a convenience, not a second protocol: a subclass satisfies `TargetAdapter`
structurally and a runner cannot tell it from a hand-written one.

What the base supplies follows from the target having no state. `reset()` and
`close()` do nothing, which makes them trivially deterministic and idempotent.
`observe()` returns no facts and `capabilities()` declares
`supports_observations=False`, because a target that changes no business state
has nothing to observe — the user-visible answer is the whole of the evidence,
and manufacturing a fact out of it would make the adapter the judge of its own
output.

**The single-turn limit is the load-bearing part.** `require_single_turn()`
refuses any input after the first of a case rather than answering it without the
turns before it, which would record an integration mismatch as a bad answer from
the model. A target that holds a conversation is not this shape: implement the
five methods directly and use `SessionStore`.

A complete integration built on it — an OpenAI-compatible API, its data
boundary, its error attribution, and the customer flow around it — is in
[`python/examples/llm-api`](../examples/llm-api/).

## Failures, and who they belong to

| Exception                   | Stage                | Attributed to             |
| --------------------------- | -------------------- | ------------------------- |
| `AdapterInternalError`      | `adapter`            | Redbeak / the integration |
| `AdapterConfigurationError` | `adapter`            | Redbeak / the integration |
| `HiddenDataError`           | `adapter`            | Redbeak / the integration |
| `UnknownSessionError`       | `adapter`            | Redbeak / the integration |
| `AdapterProtocolError`      | `protocol`           | Redbeak / the integration |
| `AdapterTransportError`     | `transport`          | Redbeak / the integration |
| `AdapterTimeoutError`       | `timeout`            | Redbeak / the integration |
| `TargetUnavailableError`    | `target_unavailable` | The system under test     |

`AdapterError` is the root of the hierarchy and therefore includes target
failures; use `is_target_failure()` rather than a class check to tell the two
apart. `normalize_exception()` turns any exception into a contract-valid
`execution_error`, and re-raises `asyncio.CancelledError` untouched — a
cancelled run must not become a pile of fabricated adapter failures.

## Observability gaps are not failures

If an adapter cannot see a requested fact, it returns fewer observations. That
is an evidence gap for the evaluator to record, not a reason to fail the case.
`ObservationRequest.missing_from(observations)` names exactly which facts went
unanswered, so the gap can be reported rather than inferred.

## Tests

```bash
uv run pytest python/redbeak-adapter-sdk
```
