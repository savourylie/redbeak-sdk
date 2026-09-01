"""Guards for the data boundary and the no-secrets rule.

`additionalProperties: false` stops a field nobody declared. It does nothing
about a field someone declares on purpose, which is the realistic failure: a
future change that adds `expected_outcome` to an assignment because it would
make an adapter easier to write. These tests fail that change at review time.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import redbeak_contracts as rc

from ._corpus import VALID

RUNNER_FACING = rc.runner_facing_schema_names()
ALL_SCHEMAS = rc.schema_names()


def test_every_schema_declares_its_boundary() -> None:
    """A new schema cannot be added without classifying it, so it cannot slip
    past the guards below by being invisible to them."""
    for name in ALL_SCHEMAS:
        assert rc.boundary_of(name) in ("runner", "internal", "shared")


def test_boundary_split_is_what_the_architecture_says() -> None:
    internal = {n for n in ALL_SCHEMAS if rc.boundary_of(n) == "internal"}
    assert internal == {
        "artifact-manifest",
        "dataset-version",
        "evaluation-result",
        "evaluator-fixture",
        "expected-evidence",
        "normalized-scenario",
        "run-manifest",
        "suite-version",
    }


@pytest.mark.parametrize("name", RUNNER_FACING)
def test_runner_facing_schema_declares_no_evaluation_private_field(name: str) -> None:
    blocked = rc.evaluation_private_property_names()
    declared = rc.declared_property_names(rc.load_schema(name))
    assert not (declared & blocked), (
        f"{name} declares evaluation-private field(s) {sorted(declared & blocked)}; "
        "ground truth, expected outcomes, rubrics, and thresholds must stay in Redbeak Cloud"
    )


@pytest.mark.parametrize("name", ALL_SCHEMAS)
def test_no_schema_declares_a_secret_like_field(name: str) -> None:
    """Applies to internal schemas too: a run manifest, event, or artifact must
    never serialise a credential either."""
    declared = rc.declared_property_names(rc.load_schema(name)) - rc.SECRET_GUARD_ALLOWLIST
    offenders = declared & rc.SECRET_LIKE_PROPERTY_NAMES
    assert not offenders, f"{name} declares secret-like field(s) {sorted(offenders)}"


def test_secret_allowlist_stays_minimal() -> None:
    """The one deliberate exception, spelled out so widening it needs a decision.

    `lease_token` is a short-lived capability scoped to a single case execution,
    not a credential at rest, and the architecture already requires it to be
    kept out of application logs.
    """
    assert {"lease_token"} == rc.SECRET_GUARD_ALLOWLIST


def test_blocklist_has_not_been_weakened() -> None:
    """The blocklist is an ordinary enum in an ordinary file. Emptying it would
    break nothing else, so assert its load-bearing entries directly."""
    blocked = rc.evaluation_private_property_names()
    for name in ("ground_truth", "expected_outcome", "rubric", "possible_answer", "future_turns"):
        assert name in blocked


def test_valid_runner_fixtures_carry_no_evaluation_private_key() -> None:
    """Data-level companion to the schema-level guard. Invalid fixtures are
    excluded: several of them exist precisely to carry a leaked key."""
    blocked = rc.evaluation_private_property_names()
    for schema, path, document in VALID:
        if rc.boundary_of(schema) == "internal":
            continue
        present = rc.data_property_names(document) & blocked
        assert not present, f"{path} carries {sorted(present)}"


def test_opaque_setup_payload_still_rejects_leaked_ground_truth() -> None:
    """The one place `additionalProperties: false` cannot reach.

    A wholesale copy of an upstream BFCL record would carry `ground_truth`
    beside `initial_config`. The recursive guard has to catch it at any depth,
    including inside an array, or the leak reaches a customer environment.
    """
    base = json.loads((rc.fixture_dir("valid") / "work-claim-response" / "leased.json").read_text())
    assert rc.is_valid("work-claim-response", base)

    def leak_at_top(payload: dict[str, Any]) -> None:
        payload["ground_truth"] = ["mv a b"]

    def leak_in_nested_object(payload: dict[str, Any]) -> None:
        payload["root"]["possible_answer"] = {"turn_0": []}

    def leak_inside_array(payload: dict[str, Any]) -> None:
        payload["nested"] = [{"deep": {"rubric": "mentions Archived"}}]

    for mutate in (leak_at_top, leak_in_nested_object, leak_inside_array):
        leaked = json.loads(json.dumps(base))
        mutate(leaked["assignment"]["setup"]["payload"])
        assert not rc.is_valid("work-claim-response", leaked)
