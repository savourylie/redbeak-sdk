"""Hidden-data leakage, checked on every input an adapter can receive.

An adapter takes exactly three things from Redbeak: a session context (which
carries the setup payload), a user input, and an observation request. This file
poisons each of them in turn, so "covered every adapter input" is a property of
the test list rather than a claim in a docstring. The fourth case here is the
adapter's *output*, because an observation carrying a verdict is the same leak
running in the other direction.
"""

from __future__ import annotations

from typing import Any

import pytest
import redbeak_contracts as rc
from _sdk_fixtures import CASE_EXECUTION_ID, PROJECT_ID, RUN_ID, SCENARIO_ID
from redbeak_adapter_sdk import (
    VERDICT_LIKE_PROPERTY_NAMES,
    HiddenDataError,
    Observation,
    ObservationRequest,
    SessionContext,
    UserInput,
    assert_no_hidden_data,
    find_hidden_data,
    hidden_property_names,
)

#: Names the contract itself forbids, sampled across its blocklist.
CONTRACT_BLOCKED = ["ground_truth", "expected_outcome", "rubric", "future_turns"]

#: Names legitimately present in BFCL mock state. A substring rule would reject
#: all of these, which is why matching is by exact name.
LEGITIMATE = ["password", "passenger", "passenger_name", "passport_number", "expiry_date"]


def context_with(payload: dict[str, Any]) -> SessionContext:
    return SessionContext(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        case_execution_id=CASE_EXECUTION_ID,
        scenario_id=SCENARIO_ID,
        scenario_external_id="multi_turn_base_3",
        setup=payload,
    )


class TestTheBlocklistItself:
    def test_is_read_out_of_the_contract(self) -> None:
        """Restating the list here would let the two drift apart silently."""
        assert rc.evaluation_private_property_names() <= hidden_property_names()

    def test_adds_the_verdict_vocabulary(self) -> None:
        assert hidden_property_names() >= VERDICT_LIKE_PROPERTY_NAMES

    @pytest.mark.parametrize("name", LEGITIMATE)
    def test_does_not_reject_legitimate_domain_names(self, name: str) -> None:
        assert find_hidden_data({name: "value"}) == ()

    def test_finds_a_blocked_name_at_any_depth(self) -> None:
        buried = {"a": [{"b": {"c": {"ground_truth": ["mv(source='x')"]}}}]}
        assert find_hidden_data(buried) == ("ground_truth",)

    def test_reports_every_offender_not_just_the_first(self) -> None:
        assert find_hidden_data({"rubric": 1, "ground_truth": 2}) == ("ground_truth", "rubric")


class TestSessionContextInput:
    @pytest.mark.parametrize("name", CONTRACT_BLOCKED)
    def test_refuses_a_poisoned_setup_payload(self, name: str) -> None:
        with pytest.raises(HiddenDataError, match=name):
            context_with({"GorillaFileSystem": {name: "leaked"}})

    @pytest.mark.parametrize("name", sorted(VERDICT_LIKE_PROPERTY_NAMES))
    def test_refuses_a_verdict_shaped_setup_payload(self, name: str) -> None:
        with pytest.raises(HiddenDataError, match=name):
            context_with({"GorillaFileSystem": {name: "leaked"}})

    def test_accepts_a_real_bfcl_setup_payload(self) -> None:
        """The travel mock carries a password and a passport; both are fine."""
        context = context_with(
            {
                "TravelAPI": {
                    "access_token": "abc123token",
                    "credit_card_list": {"1234": {"expiry_date": "12/25"}},
                },
                "TwitterAPI": {"password": "Kj8#mP9$vL2", "username": "analyst_pro"},
            }
        )
        assert "TravelAPI" in context.setup


class TestUserInputInput:
    def test_refuses_hidden_data_in_a_turn(self) -> None:
        with pytest.raises(HiddenDataError, match="ground_truth"):
            assert_no_hidden_data(
                {**UserInput(sequence=0, content="hi").to_dict(), "ground_truth": []},
                where="user input",
            )

    def test_has_no_field_a_leak_could_travel_in(self) -> None:
        """`additionalProperties: false` is the structural half of the guard."""
        assert set(UserInput(sequence=0, content="hi").to_dict()) <= {
            "sequence",
            "type",
            "content",
            "locale",
            "deadline_ms",
        }


class TestObservationRequestInput:
    def test_refuses_a_note_carrying_hidden_data(self) -> None:
        with pytest.raises(HiddenDataError, match="expected_outcome"):
            assert_no_hidden_data(
                {"fact_names": ["a_state"], "expected_outcome": True},
                where="observation request",
            )

    def test_carries_names_only(self) -> None:
        request = ObservationRequest(fact_names=("a_state",), note="after the order")
        assert set(request.to_dict()) == {"fact_names", "note"}


class TestObservationOutput:
    def test_refuses_a_verdict_nested_in_an_observed_value(self) -> None:
        """The schema stops a top-level verdict; only a name walk stops a nested one."""
        with pytest.raises(HiddenDataError, match="verdict"):
            Observation(name="a_state", value={"result": {"verdict": "pass"}})

    def test_refuses_a_score_nested_in_an_observed_value(self) -> None:
        with pytest.raises(HiddenDataError, match="score"):
            Observation(name="a_state", value=[{"score": 0.9}])

    def test_refuses_an_expected_value_nested_in_an_observed_value(self) -> None:
        with pytest.raises(HiddenDataError, match="expected_value"):
            Observation(name="a_state", value={"expected_value": 3})

    def test_allows_a_plain_domain_snapshot(self) -> None:
        observation = Observation(
            name="trading_bot_state", value={"account_info": {"balance": 10_000.0}}
        )
        assert observation.to_dict()["value"]["account_info"]["balance"] == 10_000.0
