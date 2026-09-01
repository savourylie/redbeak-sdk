"""The four rejection categories the ticket names are actually exercised.

Coverage is asserted from the validator's own error keywords rather than from
fixture filenames, so renaming a fixture cannot quietly hollow out the evidence.
"""

from __future__ import annotations

from typing import Any

import redbeak_contracts as rc
from jsonschema.exceptions import ValidationError

from ._corpus import INVALID

STATE_BEARING = {
    "case-completion",
    "evaluation-result",
    "artifact-manifest",
    "error-response",
    "next-action",
    "observation",
    "scenario-input",
}


def _keywords(error: ValidationError) -> set[str]:
    """Collect keywords from an error and everything nested inside it.

    `oneOf` and `allOf` report a wrapper error whose real cause lives in
    `.context`, so a flat read of `error.validator` would misclassify every
    branch-based schema.
    """
    found = {str(error.validator)}
    for sub in error.context or ():
        found |= _keywords(sub)
    return found


def _keywords_for(schema: str, document: Any) -> set[str]:
    found: set[str] = set()
    for error in rc.iter_errors(schema, document):
        found |= _keywords(error)
    return found


def _schemas_rejecting_with(*keywords: str) -> set[str]:
    wanted = set(keywords)
    return {schema for schema, _, document in INVALID if _keywords_for(schema, document) & wanted}


def test_unknown_fields_are_rejected_for_every_schema() -> None:
    covered = _schemas_rejecting_with("additionalProperties", "propertyNames")
    expected = {s for s, _, _ in INVALID}
    assert covered == expected, f"no unknown-field rejection for: {expected - covered}"


def test_missing_required_fields_are_rejected_for_every_schema() -> None:
    covered = _schemas_rejecting_with("required")
    expected = {s for s, _, _ in INVALID}
    assert covered == expected, f"no missing-field rejection for: {expected - covered}"


def test_invalid_state_values_are_rejected() -> None:
    covered = _schemas_rejecting_with("enum", "const")
    assert covered >= STATE_BEARING, f"no invalid-state rejection for: {STATE_BEARING - covered}"


def test_contradictory_completion_payloads_are_rejected() -> None:
    """A payload whose every field is individually legal but whose combination
    asserts two incompatible things."""
    contradictions = [
        (schema, path, document)
        for schema, path, document in INVALID
        if path.stem.startswith("contradiction-")
    ]
    by_schema: dict[str, int] = {}
    for schema, _, document in contradictions:
        assert not rc.is_valid(schema, document)
        by_schema[schema] = by_schema.get(schema, 0) + 1

    # completed+error, completed+cancel_reason, failed-without-error,
    # canceled-without-reason, canceled+error
    assert by_schema.get("case-completion", 0) >= 5
    # pass+stage, not_evaluated-without-stage, fail+infra, refused+infra, invalid+infra
    assert by_schema.get("evaluation-result", 0) >= 5
