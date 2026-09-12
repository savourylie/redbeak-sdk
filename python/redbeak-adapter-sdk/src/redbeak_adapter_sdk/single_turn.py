"""A base for the simplest system under test: one question in, one answer out.

Four of the five contract methods are identical across every stateless,
text-only target, and rewriting them per integration is how they drift. This
base supplies those four so that such an integration is one method long, and it
states in code the limit that makes them correct: **one input, one answer,
nothing kept.**

That limit is not a simplification to be lifted later by adding a dictionary. A
target that remembers a conversation has per-session state, and state has to be
isolated, reset deterministically, and released — which is exactly what the four
methods here decline to do. A stateful or multi-turn target implements the
five-method protocol directly and uses :class:`~redbeak_adapter_sdk.SessionStore`;
it does not subclass this.

The base is a convenience, not a second protocol. A subclass still satisfies
:class:`~redbeak_adapter_sdk.TargetAdapter` structurally, and a runner cannot
tell one from the other.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from redbeak_adapter_sdk.errors import AdapterConfigurationError
from redbeak_adapter_sdk.models import (
    AdapterCapabilities,
    AgentOutput,
    Observation,
    ObservationRequest,
    SessionContext,
    UserInput,
)


class SingleTurnTextAdapter(ABC):
    """A target that answers one text input per case and holds nothing.

    A subclass writes :meth:`send` and nothing else. What it must not write is
    as much of the point as what it must: no session bookkeeping to get wrong,
    no observation plumbing to accidentally turn into a scorer, no capability
    declaration that drifts from what the integration actually does.

    The declared capabilities say ``supports_observations=False`` and name no
    observable facts. That is a statement about the target rather than a missing
    feature: an API that only answers questions changes no business state, so
    there is nothing to observe and the user-visible answer is the whole of the
    evidence.
    """

    def __init__(
        self,
        *,
        adapter_name: str,
        adapter_version: str,
        target_versions: Sequence[str] = (),
        max_concurrent_cases: int = 1,
    ) -> None:
        """Declare the adapter once, at construction.

        Building :class:`AdapterCapabilities` here rather than per call means a
        name or version the contract refuses fails when the adapter is
        constructed — at discovery, before a Run is claimed — instead of once
        per case in the middle of one.
        """
        self._capabilities = AdapterCapabilities(
            adapter_name=adapter_name,
            adapter_version=adapter_version,
            max_concurrent_cases=max_concurrent_cases,
            supports_observations=False,
            supports_artifacts=False,
            target_versions=tuple(target_versions),
            observable_fact_names=(),
        )

    async def capabilities(self) -> AdapterCapabilities:
        """What this adapter supports. Does not depend on an open session."""
        return self._capabilities

    async def reset(self, context: SessionContext) -> None:
        """Nothing to reset.

        There is no per-session state to return to a known starting point, which
        makes this trivially deterministic and idempotent — the two properties
        the contract asks ``reset()`` for, so that a retried case is judged
        against the same world as the first attempt.
        """
        return None

    @abstractmethod
    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        """Answer one input. The one method an integration writes.

        Call :meth:`require_single_turn` on the input, send that text to the
        system under test, and return its user-visible reply unchanged. Raise
        :class:`~redbeak_adapter_sdk.TargetUnavailableError` only when the target
        could not be reached or did not survive the call; a weak, wrong, or empty
        answer is a completed turn for the evaluator to judge.
        """

    async def observe(
        self, request: ObservationRequest, context: SessionContext
    ) -> list[Observation]:
        """No state facts, ever.

        Returning nothing is the honest answer for a target that holds no
        business state, and the contract already has a meaning for it: the
        evaluator records an evidence gap for each requested fact rather than
        failing the case. Manufacturing a fact out of the answer text would make
        this adapter the judge of its own output.
        """
        return []

    async def close(self, context: SessionContext) -> None:
        """Nothing is held, so there is nothing to release. Safe to call twice."""
        return None

    @staticmethod
    def require_single_turn(input: UserInput) -> str:
        """The question text, refusing any turn but the first of a case.

        Without this check a multi-turn scenario would still run: turn two would
        be answered with no knowledge of turn one, and the resulting nonsense
        would be recorded as the model's answer. Refusing instead keeps an
        integration mismatch attributable to the integration, which is the line
        the whole error vocabulary exists to protect.
        """
        if input.sequence != 0:
            raise AdapterConfigurationError(
                f"this adapter is single-turn and was given turn {input.sequence}; "
                "a scenario with more than one input needs an adapter that keeps "
                "conversation history"
            )
        return input.content


__all__ = ["SingleTurnTextAdapter"]
