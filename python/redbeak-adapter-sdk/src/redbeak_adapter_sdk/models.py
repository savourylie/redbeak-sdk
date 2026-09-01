"""Typed values that cross the target-adapter boundary.

Every model here is frozen, serialises to the contract shape, and validates
itself on construction against the JSON Schema documents in ``contracts/`` — not
against a Python restatement of them. That is the same rule the contract loader
follows, for the same reason: an adapter that agreed with a hand-copied type but
disagreed with the schema would pass its own tests and fail on the wire.

Construction-time validation also carries the data boundary. ``SessionContext``,
``UserInput``, and ``ObservationRequest`` are the complete set of inputs an
adapter receives, and each refuses evaluator-private material as it is built, so
a target implementation gets that guarantee without writing a line of guard code
and cannot opt out of it by forgetting to call something.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any, ClassVar, Literal

import redbeak_contracts as rc

from redbeak_adapter_sdk.errors import AdapterConfigurationError, AdapterProtocolError
from redbeak_adapter_sdk.guards import assert_no_hidden_data

#: Where an observed fact came from. Mirrors ``observation.schema.json``.
ObservationSource = Literal["agent_output", "customer_state", "adapter_computed"]

#: Demo v0 resolves artifact-backed setup on the Redbeak side and hands the
#: adapter an inline payload. Naming the unsupported branch explicitly keeps the
#: gap visible instead of letting an artifact reference fail as a missing key.
_INLINE_SETUP = "inline"


@cache
def _pattern(definition: str) -> re.Pattern[str]:
    """A shared ``$defs`` pattern, read out of the contract rather than copied."""
    schema = rc.load_schema("common")["$defs"][definition]
    return re.compile(str(schema["pattern"]))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdapterConfigurationError(message)


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """What an adapter can do, stated explicitly and serialisably.

    A runner needs this before it claims work: the contract's work-claim request
    carries the concurrency and observation flags, and evidence carries the
    adapter version. Declaring them in one object means a target cannot be run
    under assumptions it never agreed to, and ``observable_fact_names`` lets a
    mismatch between what Redbeak asks for and what the integration can see be
    detected before a run rather than discovered as an empty observation list.

    Fact names only, never expected values: this object crosses into the
    customer environment.
    """

    adapter_name: str
    adapter_version: str
    max_concurrent_cases: int = 1
    supports_observations: bool = True
    supports_artifacts: bool = False
    target_versions: tuple[str, ...] = ()
    observable_fact_names: tuple[str, ...] = ()
    contract_version: str = rc.CONTRACT_VERSION

    def __post_init__(self) -> None:
        _require(
            bool(_pattern("slug").fullmatch(self.adapter_name)),
            f"adapter_name must be a contract slug, got {self.adapter_name!r}",
        )
        version = _pattern("version_string")
        _require(
            bool(version.fullmatch(self.adapter_version)),
            f"adapter_version must be a contract version string, got {self.adapter_version!r}",
        )
        for target_version in self.target_versions:
            _require(
                bool(version.fullmatch(target_version)),
                f"target_versions must be contract version strings, got {target_version!r}",
            )
        fact_name = _pattern("fact_name")
        for name in self.observable_fact_names:
            _require(
                bool(fact_name.fullmatch(name)),
                f"observable_fact_names must be contract fact names, got {name!r}",
            )
        _require(
            1 <= self.max_concurrent_cases <= 64,
            f"max_concurrent_cases must be between 1 and 64, got {self.max_concurrent_cases}",
        )
        _require(
            self.contract_version == rc.CONTRACT_VERSION,
            f"adapter speaks contract {self.contract_version},"
            f" this SDK speaks {rc.CONTRACT_VERSION}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "adapter_version": self.adapter_version,
            "contract_version": self.contract_version,
            "max_concurrent_cases": self.max_concurrent_cases,
            "supports_observations": self.supports_observations,
            "supports_artifacts": self.supports_artifacts,
            "target_versions": list(self.target_versions),
            "observable_fact_names": list(self.observable_fact_names),
        }

    def to_work_claim_capabilities(self) -> dict[str, Any]:
        """The subset the contract's work-claim request accepts.

        ``observable_fact_names`` and the adapter's own name stay out of it. The
        claim body is ``additionalProperties: false``, and widening what a runner
        announces about a customer integration is a contract change, not an
        adapter decision.
        """
        capabilities: dict[str, Any] = {
            "max_concurrent_cases": self.max_concurrent_cases,
            "supports_observations": self.supports_observations,
            "supports_artifacts": self.supports_artifacts,
        }
        if self.target_versions:
            capabilities["target_versions"] = list(self.target_versions)
        return capabilities

    def undeclared_fact_names(self, fact_names: Sequence[str]) -> tuple[str, ...]:
        """Requested names this adapter has *not* declared it can observe.

        Named for what it returns rather than as a predicate: a truthy result
        means a mismatch, and a method called ``can_observe`` returning that
        would read exactly backwards at the call site.

        An adapter that declared no facts at all is treated as making no claim,
        so nothing is reported as undeclared.
        """
        if not self.observable_fact_names:
            return ()
        declared = set(self.observable_fact_names)
        return tuple(name for name in fact_names if name not in declared)


@dataclass(frozen=True, slots=True)
class SessionContext:
    """Identity and setup material for one case execution.

    The session key is ``case_execution_id``: it is the unit Redbeak leases,
    retries, and completes, so isolating state by anything coarser — a run, a
    scenario — would let a retry observe the previous attempt's leftovers.
    """

    project_id: str
    run_id: str
    case_execution_id: str
    scenario_id: str
    scenario_external_id: str
    setup: Mapping[str, Any] = field(default_factory=dict)
    target_version: str | None = None

    def __post_init__(self) -> None:
        uuid = _pattern("uuid")
        for name in ("project_id", "run_id", "case_execution_id", "scenario_id"):
            value = getattr(self, name)
            _require(bool(uuid.fullmatch(value)), f"{name} must be a uuid, got {value!r}")
        _require(bool(self.scenario_external_id), "scenario_external_id must not be empty")
        if self.target_version is not None:
            _require(
                bool(_pattern("version_string").fullmatch(self.target_version)),
                f"target_version must be a contract version string, got {self.target_version!r}",
            )
        # The guard runs first deliberately. Its vocabulary is a superset of the
        # schema's, so letting the schema reject a leak first would report the
        # narrower error and hide the verdict-shaped half of the check.
        assert_no_hidden_data(dict(self.setup), where="session setup")
        rc.validate_definition("common", "leak_free_object", dict(self.setup))

    @property
    def session_id(self) -> str:
        return self.case_execution_id

    @classmethod
    def from_assignment(cls, assignment: Mapping[str, Any]) -> SessionContext:
        """Build a context from a work-claim assignment.

        Only the inline setup branch is supported. Demo v0 resolves artifact
        references on the Redbeak side, and an adapter that fetched one itself
        would need outbound access to Redbeak storage — the opposite of the
        outbound-only-to-the-target posture the architecture requires.
        """
        setup = assignment.get("setup", {})
        setup_type = setup.get("type")
        if setup_type != _INLINE_SETUP:
            raise AdapterConfigurationError(
                f"only inline setup is supported by this SDK, got {setup_type!r}"
            )
        return cls(
            project_id=str(assignment["project_id"]),
            run_id=str(assignment["run_id"]),
            case_execution_id=str(assignment["case_execution_id"]),
            scenario_id=str(assignment["scenario_id"]),
            scenario_external_id=str(assignment["scenario_external_id"]),
            setup=dict(setup.get("payload", {})),
            target_version=assignment.get("target_version"),
        )


@dataclass(frozen=True, slots=True)
class UserInput:
    """One revealed user turn.

    There is deliberately no field for the turns that follow. The contract
    reveals exactly one input at a time, and modelling a single turn rather than
    a list is what makes "the adapter never sees the future" a property of the
    type instead of a rule someone has to remember.
    """

    sequence: int
    content: str
    locale: str | None = None
    deadline_ms: int | None = None

    type: ClassVar[str] = "user_message"

    def __post_init__(self) -> None:
        rc.validate("scenario-input", self.to_dict())
        assert_no_hidden_data(self.to_dict(), where="user input")

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "sequence": self.sequence,
            "type": self.type,
            "content": self.content,
        }
        if self.locale is not None:
            document["locale"] = self.locale
        if self.deadline_ms is not None:
            document["deadline_ms"] = self.deadline_ms
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> UserInput:
        rc.validate("scenario-input", dict(document))
        return cls(
            sequence=int(document["sequence"]),
            content=str(document["content"]),
            locale=document.get("locale"),
            deadline_ms=document.get("deadline_ms"),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """A pointer to raw evidence stored outside the turn body."""

    artifact_id: str
    sha256: str
    media_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"artifact_id": self.artifact_id, "sha256": self.sha256}
        if self.media_type is not None:
            document["media_type"] = self.media_type
        return document


@dataclass(frozen=True, slots=True)
class AgentOutput:
    """The user-visible answer for one input.

    Internal model and tool steps are the adapter's business and have no field
    here. An empty answer is legal: a target that says nothing has answered
    badly, which is a completed turn for the evaluator to judge, not a failure
    for the adapter to raise.
    """

    content: str
    truncated: bool | None = None
    artifacts: tuple[ArtifactRef, ...] = ()

    type: ClassVar[str] = "agent_message"

    def __post_init__(self) -> None:
        rc.validate("agent-output", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"type": self.type, "content": self.content}
        if self.truncated is not None:
            document["truncated"] = self.truncated
        if self.artifacts:
            document["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AgentOutput:
        rc.validate("agent-output", dict(document))
        return cls(
            content=str(document["content"]),
            truncated=document.get("truncated"),
            artifacts=tuple(
                ArtifactRef(
                    artifact_id=str(item["artifact_id"]),
                    sha256=str(item["sha256"]),
                    media_type=item.get("media_type"),
                )
                for item in document.get("artifacts", [])
            ),
        )


@dataclass(frozen=True, slots=True)
class ObservationRequest:
    """The names of the facts Redbeak wants observed for this turn.

    Names only. What a fact is *supposed* to be stays in Redbeak Cloud, which is
    what keeps the adapter an integration boundary rather than a scorer the
    customer controls.
    """

    fact_names: tuple[str, ...]
    note: str | None = None

    def __post_init__(self) -> None:
        rc.validate_definition("common", "observation_request", self.to_dict())
        assert_no_hidden_data(self.to_dict(), where="observation request")

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"fact_names": list(self.fact_names)}
        if self.note is not None:
            document["note"] = self.note
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> ObservationRequest:
        rc.validate_definition("common", "observation_request", dict(document))
        return cls(fact_names=tuple(document["fact_names"]), note=document.get("note"))

    def missing_from(self, observations: Sequence[Observation]) -> tuple[str, ...]:
        """Requested names no observation answered.

        An adapter that cannot see a fact returns fewer observations rather than
        failing the case: a fact the integration cannot reach is an evidence gap
        for the evaluator to record, not a target failure. This makes the gap
        explicit so it can be recorded instead of inferred from a short list.
        """
        observed = {observation.name for observation in observations}
        return tuple(name for name in self.fact_names if name not in observed)


@dataclass(frozen=True, slots=True)
class Observation:
    """One typed fact. Evidence, never a verdict.

    The schema keeps a verdict out of the top level by refusing unknown
    properties; ``value`` is free-form because facts are domain-shaped, so the
    guard also walks it. Between them, an adapter cannot ship a score, an
    expected value, or a pass/fail alongside what it saw.
    """

    name: str
    value: Any
    subject: str | None = None
    observed_at: str | None = None
    source: ObservationSource | None = None

    def __post_init__(self) -> None:
        rc.validate("observation", self.to_dict())
        assert_no_hidden_data(self.to_dict(), where=f"observation {self.name!r}")

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {"name": self.name, "value": self.value}
        if self.subject is not None:
            document["subject"] = self.subject
        if self.observed_at is not None:
            document["observed_at"] = self.observed_at
        if self.source is not None:
            document["source"] = self.source
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> Observation:
        rc.validate("observation", dict(document))
        return cls(
            name=str(document["name"]),
            value=document["value"],
            subject=document.get("subject"),
            observed_at=document.get("observed_at"),
            source=document.get("source"),
        )


def observations_to_wire(observations: Sequence[Observation]) -> list[dict[str, Any]]:
    """Serialise observations for a turn submission, refusing duplicates.

    Two observations of the same fact in one turn is a protocol error rather
    than something to silently de-duplicate: the evaluator would have no basis
    for picking one, and quietly dropping the other would destroy evidence.
    """
    seen: set[str] = set()
    for observation in observations:
        if observation.name in seen:
            raise AdapterProtocolError(f"duplicate observation for fact {observation.name!r}")
        seen.add(observation.name)
    return [observation.to_dict() for observation in observations]


__all__ = [
    "AdapterCapabilities",
    "AgentOutput",
    "ArtifactRef",
    "Observation",
    "ObservationRequest",
    "ObservationSource",
    "SessionContext",
    "UserInput",
    "observations_to_wire",
]
