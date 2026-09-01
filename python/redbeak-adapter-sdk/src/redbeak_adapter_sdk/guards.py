"""The runtime half of the data boundary, enforced on the customer side.

The contract already forbids evaluation-private property names inside the
payloads it defines, and Redbeak Cloud validates its own outgoing bodies. That
protects the wire. It does not protect an adapter, which may be handed a payload
that never went through a validated envelope: a hand-built test double, a
replayed fixture, a proxy that rewrote the body, a future control plane with a
bug. So the adapter checks its own inputs, and the blocklist is read back out of
the contract rather than restated here, so the two cannot drift.

Matching is by exact property name, never by prefix or substring. The contract
makes the same choice and for the same reason: BFCL's own mock states contain
``password``, ``passenger``, and ``expiry_date``, and a substring rule would
reject legitimate domain data while adding no protection a name list lacks.
"""

from __future__ import annotations

from typing import Any

import redbeak_contracts as rc

from redbeak_adapter_sdk.errors import HiddenDataError

#: Verdict-shaped names, refused in addition to the contract's blocklist.
#:
#: The contract blocks the vocabulary of *expected* values. This set blocks the
#: vocabulary of *judgement*, which matters in the other direction: an
#: observation is evidence, and an adapter that shipped a score or a pass/fail
#: alongside a fact would have quietly made the customer's own code the scorer.
#: `additionalProperties: false` stops that at the top level of an observation;
#: only a name check stops it from arriving nested inside an observed value.
VERDICT_LIKE_PROPERTY_NAMES = frozenset(
    {
        "correctness",
        "expected_value",
        "expected_values",
        "grade",
        "grades",
        "is_correct",
        "pass_fail",
        "passed",
        "rubric_score",
        "score",
        "scores",
        "verdict",
        "verdicts",
    }
)


def hidden_property_names() -> frozenset[str]:
    """Every property name an adapter input may not carry, at any depth."""
    return rc.evaluation_private_property_names() | VERDICT_LIKE_PROPERTY_NAMES


def find_hidden_data(document: Any) -> tuple[str, ...]:
    """Blocked property names present in ``document``, sorted, at any depth."""
    return tuple(sorted(rc.data_property_names(document) & hidden_property_names()))


def assert_no_hidden_data(document: Any, *, where: str) -> None:
    """Refuse a payload that carries evaluator-private or verdict-shaped keys.

    Raises :class:`~redbeak_adapter_sdk.errors.HiddenDataError`, which normalises
    to an ``adapter``-stage execution error: a leak that reaches this point is a
    Redbeak-side integration fault, not a failure of the system under test.
    """
    found = find_hidden_data(document)
    if found:
        raise HiddenDataError(f"{where} carries evaluator-private material: {', '.join(found)}")


__all__ = [
    "VERDICT_LIKE_PROPERTY_NAMES",
    "assert_no_hidden_data",
    "find_hidden_data",
    "hidden_property_names",
]
