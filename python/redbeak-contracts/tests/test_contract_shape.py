"""Structural invariants that keep the contract strict as it grows."""

from __future__ import annotations

from typing import Any

import pytest
import redbeak_contracts as rc

# `leak_free_object` constrains an intentionally open-shaped payload with
# `propertyNames` plus a recursive `additionalProperties` schema, so it is
# strict in a different way and cannot satisfy the blanket rule below.
STRICTNESS_EXEMPT = {"leak_free_object"}


# `if`, `then`, `else`, and `not` use `properties` to constrain a subset of the
# fields of an object defined elsewhere. Demanding `additionalProperties: false`
# inside them would reject every field the branch does not happen to mention.
CONDITIONAL_KEYWORDS = {"if", "then", "else", "not"}


def _non_strict_object_paths(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if "properties" in node and node.get("additionalProperties") is not False:
            found.append(path)
        for key, value in node.items():
            if key in CONDITIONAL_KEYWORDS:
                continue
            found += _non_strict_object_paths(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            found += _non_strict_object_paths(item, f"{path}[{i}]")
    return found


@pytest.mark.parametrize("name", rc.schema_names())
def test_every_declared_object_is_strict(name: str) -> None:
    schema = rc.load_schema(name)
    offenders = [
        p for p in _non_strict_object_paths(schema) if not any(e in p for e in STRICTNESS_EXEMPT)
    ]
    assert not offenders, f"{name} has non-strict object(s) at {offenders}"


@pytest.mark.parametrize("name", rc.schema_names())
def test_every_schema_is_pinned_to_the_contract_version(name: str) -> None:
    schema = rc.load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"/{rc.CONTRACT_VERSION}/{name}.schema.json")
    assert schema.get("title")
    assert schema.get("description"), f"{name} has no description explaining what it is for"


def test_schema_version_constant_is_declared_by_the_contract() -> None:
    assert rc.load_schema("common")["$defs"]["schema_version"]["const"] == rc.CONTRACT_VERSION


def test_no_format_assertion_is_load_bearing() -> None:
    """Every `format` must be paired with a `pattern`.

    Format assertion is off in both validators by design; a `format` with no
    `pattern` beside it would be a constraint that silently enforces nothing.
    """
    defs = rc.load_schema("common")["$defs"]
    for name, node in defs.items():
        if isinstance(node, dict) and "format" in node:
            assert "pattern" in node, f"common.$defs.{name} has format but no pattern"
