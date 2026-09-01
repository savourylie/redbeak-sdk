"""Minimal local checkpoint for resume after interruption.

The file holds lease and sequence state so a killed runner can continue at the
server-authoritative turn. It is not evidence: ``lease_token`` is a short-lived
capability the protocol requires, and the architecture already forbids logging
it. The runner API key never belongs here — that is configuration, not case
state.
"""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from redbeak_runner.errors import CheckpointError

CHECKPOINT_NAME = "runner-checkpoint.json"
Phase = Literal["turn", "complete"]


@dataclass(frozen=True, slots=True)
class Checkpoint:
    runner_id: str
    case_execution_id: str
    lease_id: str
    lease_token: str
    assignment: dict[str, Any]
    submitted_inputs: tuple[dict[str, Any], ...]
    pending_input: dict[str, Any] | None
    pending_observation_request: dict[str, Any] | None
    pending_turn_idempotency_key: str | None
    pending_complete_idempotency_key: str | None
    phase: Phase

    def to_dict(self) -> dict[str, Any]:
        return {
            "runner_id": self.runner_id,
            "case_execution_id": self.case_execution_id,
            "lease_id": self.lease_id,
            "lease_token": self.lease_token,
            "assignment": self.assignment,
            "submitted_inputs": list(self.submitted_inputs),
            "pending_input": self.pending_input,
            "pending_observation_request": self.pending_observation_request,
            "pending_turn_idempotency_key": self.pending_turn_idempotency_key,
            "pending_complete_idempotency_key": self.pending_complete_idempotency_key,
            "phase": self.phase,
        }

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> Checkpoint:
        required = (
            "runner_id",
            "case_execution_id",
            "lease_id",
            "lease_token",
            "assignment",
            "submitted_inputs",
            "phase",
        )
        missing = [name for name in required if name not in document]
        if missing:
            raise CheckpointError(f"checkpoint missing fields: {missing}")
        phase = document["phase"]
        if phase not in ("turn", "complete"):
            raise CheckpointError(f"checkpoint has unknown phase {phase!r}")
        return cls(
            runner_id=str(document["runner_id"]),
            case_execution_id=str(document["case_execution_id"]),
            lease_id=str(document["lease_id"]),
            lease_token=str(document["lease_token"]),
            assignment=dict(document["assignment"]),
            submitted_inputs=tuple(dict(item) for item in document["submitted_inputs"]),
            pending_input=_optional_object(document.get("pending_input")),
            pending_observation_request=_optional_object(
                document.get("pending_observation_request")
            ),
            pending_turn_idempotency_key=_optional_str(
                document.get("pending_turn_idempotency_key")
            ),
            pending_complete_idempotency_key=_optional_str(
                document.get("pending_complete_idempotency_key")
            ),
            phase=phase,
        )


def checkpoint_path(directory: Path) -> Path:
    return directory / CHECKPOINT_NAME


def load_checkpoint(directory: Path) -> Checkpoint | None:
    path = checkpoint_path(directory)
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"could not read checkpoint {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise CheckpointError(f"checkpoint {path} is not an object")
    return Checkpoint.from_dict(document)


def save_checkpoint(directory: Path, checkpoint: Checkpoint) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = checkpoint_path(directory)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True)
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with suppress(OSError):
        os.chmod(path, 0o600)


def clear_checkpoint(directory: Path) -> None:
    path = checkpoint_path(directory)
    if path.is_file():
        path.unlink()


def _optional_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise CheckpointError("checkpoint field must be an object or null")
    return dict(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
