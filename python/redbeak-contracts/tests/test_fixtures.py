"""Every shared fixture is accepted or rejected as its directory claims.

These are the tests that pin the first two acceptance criteria. The same
fixtures are consumed by the TypeScript suite, so a disagreement between the
two languages shows up as a failure here or there rather than as a runtime
surprise in a customer environment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import redbeak_contracts as rc

from ._corpus import INVALID, VALID, case_id


def test_fixture_corpus_is_not_empty() -> None:
    assert len(VALID) >= 30
    assert len(INVALID) >= 60


@pytest.mark.parametrize("entry", VALID, ids=case_id)
def test_valid_fixture_is_accepted(entry: tuple[str, Path, Any]) -> None:
    schema, path, document = entry
    errors = rc.iter_errors(schema, document)
    assert not errors, f"{path} should validate against {schema}: {errors[0].message}"


@pytest.mark.parametrize("entry", INVALID, ids=case_id)
def test_invalid_fixture_is_rejected(entry: tuple[str, Path, Any]) -> None:
    schema, path, document = entry
    assert not rc.is_valid(schema, document), f"{path} must not validate against {schema}"


def test_every_schema_with_fixtures_has_both_kinds() -> None:
    valid_schemas = {s for s, _, _ in VALID}
    invalid_schemas = {s for s, _, _ in INVALID}
    assert valid_schemas == invalid_schemas

    # `common` holds definitions only; every other schema must be exercised.
    expected = set(rc.schema_names()) - {"common"}
    assert valid_schemas == expected, f"unexercised schemas: {expected - valid_schemas}"
