"""The target-adapter contract.

Five asynchronous methods, and nothing else. The contract is small because it is
the whole surface a customer has to implement, and every method added to it is a
method every future integration has to honour. What a target does internally —
how many model calls, which tools, what orchestration framework — is invisible
here by design; Redbeak requires only the user-visible output and the facts it
asked to observe.

The lifecycle for one case execution is:

``reset()`` once, then ``send()`` and ``observe()`` per turn, then ``close()``.
``capabilities()`` may be called at any point and must not depend on a session.

Two rules are load-bearing and are not expressible in the signatures:

* ``observe()`` returns facts, never a verdict. The evaluator decides whether a
  fact satisfies a hidden expected outcome; an adapter that returned a pass/fail
  would have made the customer's own code the scorer.
* A target that answers *badly* has still answered. Raise
  :class:`~redbeak_adapter_sdk.errors.TargetUnavailableError` only when the
  system under test could not be reached or did not survive the call, and let
  every other failure normalise to an adapter-stage error.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from redbeak_adapter_sdk.models import (
    AdapterCapabilities,
    AgentOutput,
    Observation,
    ObservationRequest,
    SessionContext,
    UserInput,
)


@runtime_checkable
class TargetAdapter(Protocol):
    """What Redbeak needs from a system under test."""

    async def capabilities(self) -> AdapterCapabilities:
        """Declare what this adapter supports. Must not require an open session."""
        ...

    async def reset(self, context: SessionContext) -> None:
        """Bring one session to the case's initial state.

        Must be deterministic: the same context must produce the same starting
        state every time, including on a second call for a session that is
        already open, or a retried case would be judged against a different
        world than the first attempt.
        """
        ...

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        """Give the target one user turn and return its user-visible answer."""
        ...

    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]:
        """Collect the requested facts. Facts only — no expected value, no verdict.

        Returning fewer observations than were requested is legal and is how an
        integration reports that it cannot see a fact: the evaluator records an
        evidence gap rather than the case failing.
        """
        ...

    async def close(self, context: SessionContext) -> None:
        """Release everything held for one session. Must be safe to call twice."""
        ...


__all__ = ["TargetAdapter"]
