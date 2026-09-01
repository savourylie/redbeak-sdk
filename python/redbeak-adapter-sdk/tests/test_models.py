"""What the typed boundary values promise, and where they refuse.

The recurring theme: every model is checked against the JSON Schema documents in
``contracts/``, so a model that drifted from the frozen contract fails here
rather than on the wire.
"""

from __future__ import annotations

import pytest
import redbeak_contracts as rc
from _sdk_fixtures import CASE_EXECUTION_ID, PROJECT_ID, RUN_ID, SCENARIO_ID, assignment
from jsonschema.exceptions import ValidationError
from redbeak_adapter_sdk import (
    AdapterCapabilities,
    AdapterConfigurationError,
    AdapterProtocolError,
    AgentOutput,
    ArtifactRef,
    Observation,
    ObservationRequest,
    SessionContext,
    UserInput,
    observations_to_wire,
)


class TestAdapterCapabilities:
    def test_serialises_every_declared_field(self) -> None:
        capabilities = AdapterCapabilities(
            adapter_name="toy_agent",
            adapter_version="0.2.1",
            max_concurrent_cases=4,
            target_versions=("0.1.0",),
            observable_fact_names=("gorilla_file_system_state",),
        )
        document = capabilities.to_dict()
        assert document["adapter_name"] == "toy_agent"
        assert document["adapter_version"] == "0.2.1"
        assert document["contract_version"] == rc.CONTRACT_VERSION
        assert document["observable_fact_names"] == ["gorilla_file_system_state"]

    def test_fits_the_frozen_work_claim_request(self) -> None:
        """The declared capabilities must be usable as-is by a runner.

        A capability object that could not be dropped into a claim body would
        force the runner to invent its own translation, which is exactly where
        the two would drift.
        """
        capabilities = AdapterCapabilities(
            adapter_name="golden",
            adapter_version="0.1.0",
            max_concurrent_cases=2,
            target_versions=("0.1.0",),
        )
        claim = {
            "schema_version": rc.CONTRACT_VERSION,
            "runner_id": RUN_ID,
            "runner_version": "0.1.0",
            "contract_version": rc.CONTRACT_VERSION,
            "adapter_version": capabilities.adapter_version,
            "capabilities": capabilities.to_work_claim_capabilities(),
        }
        rc.validate("work-claim-request", claim)

    def test_rejects_a_name_the_contract_would_not_accept(self) -> None:
        with pytest.raises(AdapterConfigurationError, match="slug"):
            AdapterCapabilities(adapter_name="Toy Agent", adapter_version="0.1.0")

    def test_rejects_a_fact_name_the_contract_would_not_accept(self) -> None:
        with pytest.raises(AdapterConfigurationError, match="fact name"):
            AdapterCapabilities(
                adapter_name="toy", adapter_version="0.1.0", observable_fact_names=("File-Exists",)
            )

    def test_rejects_a_contract_version_this_sdk_does_not_speak(self) -> None:
        with pytest.raises(AdapterConfigurationError, match="contract"):
            AdapterCapabilities(adapter_name="toy", adapter_version="0.1.0", contract_version="9.9")

    def test_reports_facts_it_has_not_declared(self) -> None:
        """A truthy result means a mismatch, which is why it is not named as a predicate."""
        capabilities = AdapterCapabilities(
            adapter_name="toy",
            adapter_version="0.1.0",
            observable_fact_names=("trading_bot_state",),
        )
        assert capabilities.undeclared_fact_names(["trading_bot_state", "twitter_api_state"]) == (
            "twitter_api_state",
        )

    def test_declaring_nothing_claims_nothing(self) -> None:
        """An adapter that lists no facts is silent, not incapable."""
        capabilities = AdapterCapabilities(adapter_name="toy", adapter_version="0.1.0")
        assert capabilities.undeclared_fact_names(["anything_at_all"]) == ()


class TestSessionContext:
    def test_builds_from_a_contract_assignment(self) -> None:
        context = SessionContext.from_assignment(assignment())
        assert context.session_id == CASE_EXECUTION_ID
        assert context.project_id == PROJECT_ID
        assert context.setup == {"GorillaFileSystem": {"root": {}}}

    def test_session_is_keyed_by_case_execution_not_by_run(self) -> None:
        """A retry of the same scenario must not inherit the previous attempt."""
        context = SessionContext.from_assignment(assignment())
        assert context.session_id != context.run_id
        assert context.session_id != context.scenario_id

    def test_refuses_artifact_backed_setup(self) -> None:
        document = assignment()
        document["setup"] = {
            "type": "artifact_ref",
            "artifact": {"artifact_id": SCENARIO_ID, "sha256": "sha256:" + "0" * 64},
        }
        with pytest.raises(AdapterConfigurationError, match="inline"):
            SessionContext.from_assignment(document)

    def test_refuses_an_identifier_the_contract_would_not_accept(self) -> None:
        with pytest.raises(AdapterConfigurationError, match="uuid"):
            SessionContext(
                project_id="not-a-uuid",
                run_id=RUN_ID,
                case_execution_id=CASE_EXECUTION_ID,
                scenario_id=SCENARIO_ID,
                scenario_external_id="multi_turn_base_3",
            )


class TestUserInput:
    def test_round_trips_through_the_contract(self) -> None:
        original = UserInput(sequence=2, content="unlock the doors", locale="en-GB")
        assert UserInput.from_dict(original.to_dict()) == original

    def test_carries_one_turn_and_has_nowhere_to_put_another(self) -> None:
        """The type is the guarantee: there is no field for a future turn."""
        assert set(UserInput(sequence=0, content="hi").to_dict()) == {
            "sequence",
            "type",
            "content",
        }

    def test_rejects_empty_content_the_contract_forbids(self) -> None:
        with pytest.raises(ValidationError):
            UserInput(sequence=0, content="")


class TestAgentOutput:
    def test_round_trips_with_artifacts(self) -> None:
        output = AgentOutput(
            content="done",
            truncated=False,
            artifacts=(ArtifactRef(artifact_id=SCENARIO_ID, sha256="sha256:" + "a" * 64),),
        )
        assert AgentOutput.from_dict(output.to_dict()) == output

    def test_an_empty_answer_is_legal(self) -> None:
        """A target that says nothing has answered badly, not failed."""
        assert AgentOutput(content="").to_dict()["content"] == ""


class TestObservationRequest:
    def test_round_trips_through_the_shared_definition(self) -> None:
        request = ObservationRequest(fact_names=("trading_bot_state",), note="after the order")
        assert ObservationRequest.from_dict(request.to_dict()) == request

    def test_reports_the_facts_nobody_answered(self) -> None:
        request = ObservationRequest(fact_names=("a_state", "b_state"))
        observed = [Observation(name="a_state", value={})]
        assert request.missing_from(observed) == ("b_state",)

    def test_rejects_an_empty_request(self) -> None:
        with pytest.raises(ValidationError):
            ObservationRequest(fact_names=())


class TestObservation:
    def test_round_trips_through_the_contract(self) -> None:
        observation = Observation(
            name="file_exists",
            value=True,
            subject="documents/Archived/IdeasArchive.txt",
            source="customer_state",
        )
        assert Observation.from_dict(observation.to_dict()) == observation

    def test_a_domain_shaped_value_is_allowed(self) -> None:
        observation = Observation(name="trading_bot_state", value={"balance": 10_000.0})
        assert observation.to_dict()["value"] == {"balance": 10_000.0}

    def test_rejects_a_fact_name_the_contract_would_not_accept(self) -> None:
        with pytest.raises(ValidationError):
            Observation(name="File Exists", value=True)


class TestObservationsToWire:
    def test_serialises_in_order(self) -> None:
        wire = observations_to_wire(
            [Observation(name="a_state", value=1), Observation(name="b_state", value=2)]
        )
        assert [item["name"] for item in wire] == ["a_state", "b_state"]

    def test_refuses_two_observations_of_the_same_fact(self) -> None:
        """De-duplicating silently would destroy evidence the evaluator needs."""
        with pytest.raises(AdapterProtocolError, match="duplicate"):
            observations_to_wire(
                [Observation(name="a_state", value=1), Observation(name="a_state", value=2)]
            )

    def test_fits_a_turn_submission(self) -> None:
        submission = {
            "schema_version": rc.CONTRACT_VERSION,
            "lease_token": "x" * 32,
            "sequence": 0,
            "idempotency_key": SCENARIO_ID,
            "output": AgentOutput(content="done").to_dict(),
            "observations": observations_to_wire([Observation(name="a_state", value=1)]),
            "operational": {"duration_ms": 12},
        }
        rc.validate("turn-submission", submission)
