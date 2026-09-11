"""Lease, idempotency, and authorization state for the reference server."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

import redbeak_contracts as rc
from jsonschema.exceptions import ValidationError
from redbeak_runner.ids import new_lease_token, new_runner_key, new_uuid

CaseStatus = Literal["queued", "leased", "running", "submitted", "canceled"]
LEASE_TTL = timedelta(seconds=30)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FrozenClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)


def isoformat(moment: datetime) -> str:
    utc = moment.astimezone(UTC).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def error_body(code: str, message: str, retry_after_ms: int | None = None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": rc.CONTRACT_VERSION,
        "code": code,
        "message": message,
    }
    if retry_after_ms is not None:
        document["retry_after_ms"] = retry_after_ms
    rc.validate("error-response", document)
    return document


@dataclass
class RunnerKeyRecord:
    key_id: str
    project_id: str
    token_hash: str
    prefix: str
    revoked: bool = False


@dataclass
class Lease:
    lease_id: str
    lease_token: str
    runner_id: str
    case_execution_id: str
    expires_at: datetime
    superseded: bool = False


@dataclass
class TurnRecord:
    sequence: int
    idempotency_key: str
    event: dict[str, Any]
    next_action: dict[str, Any]


@dataclass
class CompletionRecord:
    idempotency_key: str
    event: dict[str, Any]
    accepted: dict[str, Any]


@dataclass
class CaseRecord:
    spec: QueuedCase
    status: CaseStatus = "queued"
    lease: Lease | None = None
    expected_sequence: int = 0
    turns: list[TurnRecord] = field(default_factory=list)
    completion: CompletionRecord | None = None
    claimed_by: str | None = None


@dataclass(frozen=True, slots=True)
class QueuedCase:
    project_id: str
    run_id: str
    case_execution_id: str
    scenario_id: str
    scenario_external_id: str
    setup: dict[str, Any]
    turns: tuple[dict[str, Any], ...]
    observation_request: dict[str, Any] | None = None
    target_version: str | None = None

    def assignment(self, *, sequence: int = 0) -> dict[str, Any]:
        if sequence != 0:
            raise ValueError("a claim assignment always reveals sequence 0")
        first = dict(self.turns[0])
        document: dict[str, Any] = {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "case_execution_id": self.case_execution_id,
            "scenario_id": self.scenario_id,
            "scenario_external_id": self.scenario_external_id,
            "sequence": 0,
            "setup": dict(self.setup),
            "input": first,
        }
        if self.target_version is not None:
            document["target_version"] = self.target_version
        if self.observation_request is not None:
            document["observation_request"] = dict(self.observation_request)
        return document


@dataclass
class WorkFile:
    project_id: str
    run_id: str
    cases: tuple[QueuedCase, ...]


def load_work_file(path: Path) -> WorkFile:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} is not an object")
    project_id = str(document["project_id"])
    run_id = str(document["run_id"])
    cases: list[QueuedCase] = []
    for item in document["cases"]:
        cases.append(
            QueuedCase(
                project_id=str(item.get("project_id", project_id)),
                run_id=str(item.get("run_id", run_id)),
                case_execution_id=str(item["case_execution_id"]),
                scenario_id=str(item["scenario_id"]),
                scenario_external_id=str(item["scenario_external_id"]),
                setup=dict(item["setup"]),
                turns=tuple(dict(turn) for turn in item["turns"]),
                observation_request=(
                    dict(item["observation_request"]) if item.get("observation_request") else None
                ),
                target_version=item.get("target_version"),
            )
        )
    return WorkFile(project_id=project_id, run_id=run_id, cases=tuple(cases))


class ReferenceServer:
    """In-memory orchestrator implementing runner protocol v0."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        lease_ttl: timedelta = LEASE_TTL,
        retry_after_ms: int = 250,
    ) -> None:
        self.clock = clock or SystemClock()
        self.lease_ttl = lease_ttl
        self.retry_after_ms = retry_after_ms
        self._keys: dict[str, RunnerKeyRecord] = {}
        self._cases: dict[str, CaseRecord] = {}
        self._leases: dict[str, Lease] = {}

    def create_key(self, project_id: str) -> str:
        raw = new_runner_key()
        record = RunnerKeyRecord(
            key_id=new_uuid(),
            project_id=project_id,
            token_hash=_hash_token(raw),
            prefix=raw[:12],
        )
        self._keys[record.token_hash] = record
        return raw

    def revoke_key(self, raw_key: str) -> None:
        record = self._keys.get(_hash_token(raw_key))
        if record is not None:
            record.revoked = True

    def enqueue(self, case: QueuedCase) -> None:
        if not case.turns:
            raise ValueError(f"{case.scenario_external_id} has no turns")
        if case.case_execution_id in self._cases:
            raise ValueError(f"duplicate case_execution_id {case.case_execution_id}")
        self._cases[case.case_execution_id] = CaseRecord(spec=case)

    def load_work(self, work: WorkFile) -> None:
        for case in work.cases:
            self.enqueue(case)

    def authenticate(self, raw_key: str | None) -> tuple[int, dict[str, Any]] | RunnerKeyRecord:
        if not raw_key:
            return 401, error_body("unauthorized", "Missing runner key.")
        record = self._keys.get(_hash_token(raw_key))
        if record is None or record.revoked:
            return 401, error_body("unauthorized", "Unknown or revoked runner key.")
        return record

    def claim(self, raw_key: str | None, body: Any) -> tuple[int, dict[str, Any]]:
        auth = self.authenticate(raw_key)
        if isinstance(auth, tuple):
            return auth
        invalid = _validate("work-claim-request", body)
        if invalid is not None:
            return invalid
        if body["contract_version"] != rc.CONTRACT_VERSION:
            return 400, error_body(
                "contract_version_unsupported",
                "This server speaks contract 0.1.",
            )
        self._expire_leases()
        runner_id = str(body["runner_id"])
        existing = self._active_lease_for(runner_id, auth.project_id)
        if existing is not None:
            return 200, self._leased_payload(existing)
        for record in self._cases.values():
            if record.status != "queued":
                continue
            if record.spec.project_id != auth.project_id:
                continue
            lease = self._grant_lease(record, runner_id)
            return 200, self._leased_payload(lease)
        payload = {
            "schema_version": rc.CONTRACT_VERSION,
            "status": "no_work",
            "retry_after_ms": self.retry_after_ms,
        }
        rc.validate("work-claim-response", payload)
        return 200, payload

    def submit_turn(
        self, raw_key: str | None, case_execution_id: str, body: Any
    ) -> tuple[int, dict[str, Any]]:
        auth = self.authenticate(raw_key)
        if isinstance(auth, tuple):
            return auth
        invalid = _validate("turn-submission", body)
        if invalid is not None:
            return invalid
        record, status = self._authorized_case(auth, case_execution_id)
        if status is not None:
            return status
        assert record is not None
        lease_status = self._require_lease(record, str(body["lease_token"]))
        if lease_status is not None:
            return lease_status
        if record.status in {"submitted", "canceled"}:
            if record.completion and record.completion.idempotency_key == body.get(
                "idempotency_key"
            ):
                return 409, error_body("already_completed", "This case execution is terminal.")
            return 409, error_body("already_completed", "This case execution is terminal.")

        sequence = int(body["sequence"])
        idempotency_key = str(body["idempotency_key"])
        for turn in record.turns:
            if turn.idempotency_key == idempotency_key:
                return 200, turn.next_action
        for turn in record.turns:
            if turn.sequence == sequence:
                return 200, turn.next_action
        if sequence != record.expected_sequence:
            return 409, error_body(
                "sequence_conflict",
                "Turn sequence does not match the authoritative cursor.",
            )
        next_action = self._next_action(record, sequence)
        event = {key: value for key, value in dict(body).items() if key != "lease_token"}
        record.turns.append(
            TurnRecord(
                sequence=sequence,
                idempotency_key=idempotency_key,
                event=event,
                next_action=next_action,
            )
        )
        record.expected_sequence = sequence + 1
        record.status = "running"
        if record.lease is not None:
            record.lease.expires_at = self.clock.now() + self.lease_ttl
        rc.validate("next-action", next_action)
        return 200, next_action

    def complete(
        self, raw_key: str | None, case_execution_id: str, body: Any
    ) -> tuple[int, dict[str, Any]]:
        auth = self.authenticate(raw_key)
        if isinstance(auth, tuple):
            return auth
        invalid = _validate("case-completion", body)
        if invalid is not None:
            return invalid
        record, status = self._authorized_case(auth, case_execution_id)
        if status is not None:
            return status
        assert record is not None
        idempotency_key = str(body["idempotency_key"])
        if record.completion is not None:
            if record.completion.idempotency_key == idempotency_key:
                return 202, record.completion.accepted
            return 409, error_body("already_completed", "This case execution is terminal.")
        lease_status = self._require_lease(record, str(body["lease_token"]))
        if lease_status is not None:
            return lease_status
        status_value = str(body["status"])
        if status_value == "completed":
            record.status = "submitted"
            accepted_status = "evaluation_queued"
        elif status_value == "canceled":
            record.status = "canceled"
            accepted_status = "recorded"
        else:
            record.status = "submitted"
            accepted_status = "recorded"
        accepted = {
            "schema_version": rc.CONTRACT_VERSION,
            "case_execution_id": case_execution_id,
            "status": accepted_status,
        }
        rc.validate("completion-accepted", accepted)
        record.completion = CompletionRecord(
            idempotency_key=idempotency_key,
            event={key: value for key, value in dict(body).items() if key != "lease_token"},
            accepted=accepted,
        )
        if record.lease is not None:
            record.lease.superseded = True
            record.lease = None
        return 202, accepted

    def heartbeat(
        self, raw_key: str | None, case_execution_id: str, body: Any
    ) -> tuple[int, dict[str, Any]]:
        auth = self.authenticate(raw_key)
        if isinstance(auth, tuple):
            return auth
        invalid = _validate("heartbeat-request", body)
        if invalid is not None:
            return invalid
        record, status = self._authorized_case(auth, case_execution_id)
        if status is not None:
            return status
        assert record is not None
        lease_status = self._require_lease(record, str(body["lease_token"]))
        if lease_status is not None:
            return lease_status
        assert record.lease is not None
        if record.status in {"submitted", "canceled"}:
            return 409, error_body("already_completed", "This case execution is terminal.")
        record.lease.expires_at = self.clock.now() + self.lease_ttl
        payload = {
            "schema_version": rc.CONTRACT_VERSION,
            "lease_id": record.lease.lease_id,
            "expires_at": isoformat(record.lease.expires_at),
        }
        rc.validate("heartbeat-response", payload)
        return 200, payload

    def events_for(self, case_execution_id: str) -> list[dict[str, Any]]:
        record = self._cases.get(case_execution_id)
        if record is None:
            return []
        events = [dict(turn.event) for turn in record.turns]
        if record.completion is not None:
            events.append(dict(record.completion.event))
        return events

    def case(self, case_execution_id: str) -> CaseRecord:
        return self._cases[case_execution_id]

    def _authorized_case(
        self, key: RunnerKeyRecord, case_execution_id: str
    ) -> tuple[CaseRecord | None, tuple[int, dict[str, Any]] | None]:
        self._expire_leases()
        record = self._cases.get(case_execution_id)
        if record is None:
            return None, (404, error_body("not_found", "No such case execution."))
        if record.spec.project_id != key.project_id:
            return None, (
                403,
                error_body("forbidden", "Runner key is not scoped to this project."),
            )
        return record, None

    def _require_lease(self, record: CaseRecord, token: str) -> tuple[int, dict[str, Any]] | None:
        lease = record.lease
        if lease is None:
            historic = [
                item
                for item in self._leases.values()
                if item.case_execution_id == record.spec.case_execution_id
                and item.lease_token == token
            ]
            if historic:
                latest = historic[-1]
                if latest.superseded:
                    return 409, error_body("lease_invalid", "This lease has been superseded.")
                return 410, error_body(
                    "lease_expired",
                    "The lease expired; claim again instead of resubmitting.",
                )
            return 409, error_body("lease_invalid", "Lease token does not match.")
        if lease.lease_token != token:
            other = next(
                (
                    item
                    for item in self._leases.values()
                    if item.lease_token == token
                    and item.case_execution_id == record.spec.case_execution_id
                ),
                None,
            )
            if other is not None and other.superseded:
                return 409, error_body("lease_invalid", "This lease has been superseded.")
            return 409, error_body("lease_invalid", "Lease token does not match.")
        if lease.expires_at <= self.clock.now():
            self._expire_one(record)
            return 410, error_body(
                "lease_expired",
                "The lease expired; claim again instead of resubmitting.",
            )
        return None

    def _grant_lease(self, record: CaseRecord, runner_id: str) -> Lease:
        for previous in self._leases.values():
            if previous.case_execution_id == record.spec.case_execution_id:
                previous.superseded = True
        lease = Lease(
            lease_id=new_uuid(),
            lease_token=new_lease_token(),
            runner_id=runner_id,
            case_execution_id=record.spec.case_execution_id,
            expires_at=self.clock.now() + self.lease_ttl,
        )
        record.lease = lease
        record.status = "leased"
        record.claimed_by = runner_id
        self._leases[lease.lease_id] = lease
        return lease

    def _active_lease_for(self, runner_id: str, project_id: str) -> Lease | None:
        for record in self._cases.values():
            if (
                record.lease is not None
                and record.lease.runner_id == runner_id
                and record.spec.project_id == project_id
                and not record.lease.superseded
                and record.status in {"leased", "running"}
            ):
                return record.lease
        return None

    def _leased_payload(self, lease: Lease) -> dict[str, Any]:
        record = self._cases[lease.case_execution_id]
        payload = {
            "schema_version": rc.CONTRACT_VERSION,
            "status": "leased",
            "lease": {
                "lease_id": lease.lease_id,
                "lease_token": lease.lease_token,
                "expires_at": isoformat(lease.expires_at),
            },
            "assignment": record.spec.assignment(),
        }
        rc.validate("work-claim-response", payload)
        return payload

    def _next_action(self, record: CaseRecord, sequence: int) -> dict[str, Any]:
        turns = record.spec.turns
        if sequence + 1 < len(turns):
            action: dict[str, Any] = {
                "schema_version": rc.CONTRACT_VERSION,
                "action": "continue",
                "input": dict(turns[sequence + 1]),
            }
            if record.spec.observation_request is not None:
                action["observation_request"] = dict(record.spec.observation_request)
            if record.lease is not None:
                action["lease_expires_at"] = isoformat(record.lease.expires_at)
            return action
        action = {"schema_version": rc.CONTRACT_VERSION, "action": "finish"}
        if record.spec.observation_request is not None:
            action["observation_request"] = dict(record.spec.observation_request)
        if record.lease is not None:
            action["lease_expires_at"] = isoformat(record.lease.expires_at)
        return action

    def _expire_leases(self) -> None:
        now = self.clock.now()
        for record in list(self._cases.values()):
            if record.lease is not None and record.lease.expires_at <= now:
                self._expire_one(record)

    def _expire_one(self, record: CaseRecord) -> None:
        if record.lease is None:
            return
        record.lease.superseded = False
        record.lease.expires_at = self.clock.now()
        self._leases[record.lease.lease_id] = record.lease
        record.lease = None
        if record.status in {"leased", "running"}:
            record.status = "queued"
            record.claimed_by = None


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _validate(schema: str, body: Any) -> tuple[int, dict[str, Any]] | None:
    if not isinstance(body, dict):
        return 400, error_body("payload_invalid", "Request body must be a JSON object.")
    try:
        rc.validate(schema, body)
    except ValidationError as exc:
        return 400, error_body("payload_invalid", exc.message[:2000] or "Invalid payload.")
    return None
