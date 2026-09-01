"""Write item-level raw evidence after the server accepts it.

The runner preserves raw turn events and observations before any parser or
scorer sees them. Manifests are content-addressed and produced_by ``runner`` so
later evaluation can point at the exact bytes. Lease tokens never enter these
files: they are capabilities, not evidence.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import redbeak_contracts as rc

_NAMESPACE = uuid.UUID("b3e7c2a1-5d84-4f73-9012-4d8e0f3c6b92")


def _stable_id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def strip_capability(document: MappingLike) -> dict[str, Any]:
    return {key: value for key, value in dict(document).items() if key != "lease_token"}


MappingLike = dict[str, Any]


class ArtifactStore:
    """Per-run directory of turn events, raw observations, and manifests."""

    def __init__(self, root: Path, *, created_at: str) -> None:
        self.root = root
        self.created_at = created_at
        root.mkdir(parents=True, exist_ok=True)

    def case_dir(self, run_id: str, case_execution_id: str) -> Path:
        path = self.root / run_id / "cases" / case_execution_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_turn(
        self,
        *,
        run_id: str,
        case_execution_id: str,
        sequence: int,
        submission: dict[str, Any],
        next_action: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = {
            "submission": strip_capability(submission),
            "next_action": dict(next_action),
        }
        return self._write(
            run_id=run_id,
            case_execution_id=case_execution_id,
            stem=f"turn-{sequence:03d}",
            role="turn_event",
            document=evidence,
            parent_ids=(),
        )

    def write_observations(
        self,
        *,
        run_id: str,
        case_execution_id: str,
        sequence: int,
        observations: list[dict[str, Any]],
        parent_artifact_id: str,
    ) -> dict[str, Any]:
        return self._write(
            run_id=run_id,
            case_execution_id=case_execution_id,
            stem=f"observations-{sequence:03d}",
            role="raw_observation",
            document={"sequence": sequence, "observations": observations},
            parent_ids=(parent_artifact_id,),
        )

    def write_completion(
        self,
        *,
        run_id: str,
        case_execution_id: str,
        completion: dict[str, Any],
        accepted: dict[str, Any],
        parent_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        evidence = {
            "completion": strip_capability(completion),
            "accepted": dict(accepted),
        }
        return self._write(
            run_id=run_id,
            case_execution_id=case_execution_id,
            stem="completion",
            role="turn_event",
            document=evidence,
            parent_ids=parent_ids,
        )

    def _write(
        self,
        *,
        run_id: str,
        case_execution_id: str,
        stem: str,
        role: str,
        document: dict[str, Any],
        parent_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        data = _canonical(document)
        digest = _sha256(data)
        artifact_id = _stable_id(run_id, case_execution_id, stem, digest)
        directory = self.case_dir(run_id, case_execution_id)
        body_name = f"{stem}.json"
        (directory / body_name).write_bytes(data)
        manifest = {
            "schema_version": rc.CONTRACT_VERSION,
            "artifact_id": artifact_id,
            "sha256": digest,
            "media_type": "application/json",
            "size_bytes": len(data),
            "role": role,
            "produced_by": "runner",
            "data_classification": "synthetic",
            "created_at": self.created_at,
            "parent_artifact_ids": list(parent_ids),
            "run_id": run_id,
            "case_execution_id": case_execution_id,
            "storage_ref": f"file:{body_name}",
        }
        rc.validate("artifact-manifest", manifest)
        (directory / f"{stem}.manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest
