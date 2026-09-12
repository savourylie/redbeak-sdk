"""Snapshot the canonical schemas into both distributions, without fixtures."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    # Consuming workspaces type-check this source without installing build tools.
    class BuildHookInterface(Protocol):
        root: str
        target_name: str
else:
    from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ContractBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        project = Path(self.root)
        package = project / "src" / "redbeak_contracts"
        bundled = package / "_contracts"
        # An sdist is self-contained. Never prefer an unrelated checkout above it.
        source = bundled if bundled.is_dir() else project.parent.parent / "contracts"
        schemas = sorted((source / "json-schema").rglob("*.json"))
        if not schemas:
            raise RuntimeError(f"No contract schemas found in {source}")

        if self.target_name == "wheel" and version == "editable":
            # Preserve live source consumption in uv workspaces, including submodules.
            # This resource is explicitly excluded from both published artifacts.
            (package / "_editable_contracts_root.txt").write_text(str(source.resolve()), "utf-8")
            return

        self._staging: TemporaryDirectory[str] = TemporaryDirectory(prefix="redbeak-contracts-")
        staged = Path(self._staging.name)
        digests = {}
        for schema in schemas:
            relative = schema.relative_to(source / "json-schema")
            destination = staged / "json-schema" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(schema, destination)
            digests[relative.as_posix()] = hashlib.sha256(destination.read_bytes()).hexdigest()
        manifest = {"algorithm": "sha256", "schemas": digests}
        if source == bundled:
            original = json.loads((bundled / "schema-manifest.json").read_text("utf-8"))
            if original != manifest:
                raise RuntimeError("The sdist schemas do not match their SHA-256 manifest")
        (staged / "schema-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8"
        )
        destination_root = (
            "src/redbeak_contracts/_contracts"
            if self.target_name == "sdist"
            else "redbeak_contracts/_contracts"
        )
        build_data["force_include"][str(staged)] = destination_root

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        if hasattr(self, "_staging"):
            self._staging.cleanup()
