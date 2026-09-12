"""Loader, validator, and boundary guards for the versioned Redbeak contract.

The JSON Schema documents under ``contracts/json-schema/`` are the single source
of truth. This package does not restate them as Python types: it loads them and
validates against them, so that the TypeScript and Python sides can never drift
into disagreeing about what the wire accepts.

Format assertions are deliberately off. Every constraint that matters is
expressed as a ``pattern``, because ``format`` is advisory by default in both
ecosystems and enabling it would make the two validators disagree.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from functools import cache
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

CONTRACT_VERSION = "0.1"

Boundary = Literal["runner", "internal", "shared"]
FixtureKind = Literal["valid", "invalid"]

#: Property names that must never be serialised into any contract payload.
#: ``lease_token`` is the one deliberate exception: the runner protocol cannot
#: work without it. It is a short-lived capability scoped to a single case
#: execution rather than a credential at rest, and the architecture already
#: requires it to be excluded from application logs.
SECRET_LIKE_PROPERTY_NAMES = frozenset(
    {
        "password",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "credential",
        "credentials",
        "authorization",
        "bearer",
        "private_key",
        "service_key",
        "access_token",
        "refresh_token",
        "session_token",
    }
)

SECRET_GUARD_ALLOWLIST = frozenset({"lease_token"})


class ContractError(RuntimeError):
    """Raised when the contract itself cannot be loaded."""


def contracts_root() -> Traversable:
    """Locate bundled contracts, or the explicitly selected development source.

    ``REDBEAK_CONTRACTS_ROOT`` wins when set, which is what lets an installed
    wheel point at a checkout. Editable builds record the live source location;
    regular wheels read their own resources without relying on a checkout.
    """
    override = os.environ.get("REDBEAK_CONTRACTS_ROOT")
    if override:
        root = Path(override)
        if not (root / "json-schema").is_dir():
            raise ContractError(f"REDBEAK_CONTRACTS_ROOT={override} has no json-schema directory")
        return root

    package = files("redbeak_contracts")
    editable = package.joinpath("_editable_contracts_root.txt")
    root_resource: Traversable = (
        Path(editable.read_text(encoding="utf-8"))
        if editable.is_file()
        else package.joinpath("_contracts")
    )
    if not root_resource.joinpath("json-schema").is_dir():
        raise ContractError("contract schemas are missing; rebuild or reinstall redbeak-contracts")
    return root_resource


def schema_dir(version: str = CONTRACT_VERSION) -> Traversable:
    return contracts_root().joinpath("json-schema", version)


def fixture_dir(kind: FixtureKind) -> Path:
    """Source-only test fixtures; intentionally absent from distributions."""
    root = contracts_root()
    if isinstance(root, Path) and (root / "fixtures" / kind).is_dir():
        return root / "fixtures" / kind
    raise ContractError("fixtures are source-only; set REDBEAK_CONTRACTS_ROOT to a checkout")


@cache
def schema_names(version: str = CONTRACT_VERSION) -> tuple[str, ...]:
    """Every schema in the contract, including the shared definitions."""
    return tuple(
        sorted(
            p.name.removesuffix(".schema.json")
            for p in schema_dir(version).iterdir()
            if p.is_file() and p.name.endswith(".schema.json")
        )
    )


@cache
def load_schema(name: str, version: str = CONTRACT_VERSION) -> dict[str, Any]:
    path = schema_dir(version).joinpath(f"{name}.schema.json")
    if not path.is_file():
        raise ContractError(f"no such schema: {name} (contract {version})")
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def boundary_of(name: str, version: str = CONTRACT_VERSION) -> Boundary:
    """Which side of the trust boundary a schema belongs to.

    Recorded in the schema file itself rather than in a list here, so that
    adding a schema cannot silently add an unclassified one.
    """
    schema = load_schema(name, version)
    value = schema.get("x-redbeak-boundary")
    if value not in ("runner", "internal", "shared"):
        raise ContractError(f"{name} has no valid x-redbeak-boundary annotation")
    return cast(Boundary, value)


def runner_facing_schema_names(version: str = CONTRACT_VERSION) -> tuple[str, ...]:
    """Schemas whose content can cross into a customer environment.

    ``shared`` counts: its definitions are pulled into runner-facing schemas by
    ``$ref``, so a leak declared there would cross the boundary too.
    """
    return tuple(
        n for n in schema_names(version) if boundary_of(n, version) in ("runner", "shared")
    )


@cache
def _registry(version: str = CONTRACT_VERSION) -> Registry:
    resources = []
    for name in schema_names(version):
        schema = load_schema(name, version)
        schema_id = schema.get("$id")
        if not schema_id:
            raise ContractError(f"{name} has no $id")
        resources.append((schema_id, Resource.from_contents(schema)))
    return Registry().with_resources(resources)


@cache
def validator_for(name: str, version: str = CONTRACT_VERSION) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name, version), registry=_registry(version))


@cache
def validator_for_definition(
    name: str, definition: str, version: str = CONTRACT_VERSION
) -> Draft202012Validator:
    """Validator for one ``$defs`` entry inside a schema document.

    Several contract components exist only as shared definitions —
    ``observation_request`` and ``execution_error`` among them — because they are
    embedded in envelopes rather than sent as one. Validating them still has to
    go through the same document that the envelopes ``$ref``, not a fragment
    copied into Python, or the two could disagree about an embedded payload
    while every envelope test still passed.
    """
    schema = load_schema(name, version)
    if definition not in schema.get("$defs", {}):
        raise ContractError(f"{name} has no $defs/{definition}")
    return Draft202012Validator(
        {"$ref": f"{schema['$id']}#/$defs/{definition}"}, registry=_registry(version)
    )


def validate_definition(
    name: str, definition: str, document: Any, version: str = CONTRACT_VERSION
) -> None:
    """Raise :class:`jsonschema.ValidationError` if ``document`` is not valid."""
    validator_for_definition(name, definition, version).validate(document)


def validate(name: str, document: Any, version: str = CONTRACT_VERSION) -> None:
    """Raise :class:`jsonschema.ValidationError` if ``document`` is not valid."""
    validator_for(name, version).validate(document)


def is_valid(name: str, document: Any, version: str = CONTRACT_VERSION) -> bool:
    return bool(validator_for(name, version).is_valid(document))


def iter_errors(name: str, document: Any, version: str = CONTRACT_VERSION) -> list[ValidationError]:
    return sorted(validator_for(name, version).iter_errors(document), key=lambda e: list(e.path))


def evaluation_private_property_names(version: str = CONTRACT_VERSION) -> frozenset[str]:
    """The blocklist, read back out of the contract rather than duplicated here."""
    common = load_schema("common", version)
    names = common["$defs"]["evaluation_private_property_names"]["enum"]
    return frozenset(names)


def iter_fixtures(kind: FixtureKind) -> Iterator[tuple[str, Path, Any]]:
    """Yield ``(schema_name, path, document)`` for every shared fixture."""
    root = fixture_dir(kind)
    for path in sorted(root.rglob("*.json")):
        yield path.parent.name, path, json.loads(path.read_text())


def declared_property_names(schema: Any) -> set[str]:
    """Every property name a schema declares, at any depth.

    This is what ``additionalProperties: false`` cannot protect against: a field
    someone deliberately adds. Walking the schema catches it at review time.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                found.update(props.keys())
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for sub in value.values():
                        walk(sub)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return found


def data_property_names(document: Any) -> set[str]:
    """Every key present in a document, at any depth."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            found.update(k for k in node if isinstance(k, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    return found


__all__ = [
    "CONTRACT_VERSION",
    "SECRET_GUARD_ALLOWLIST",
    "SECRET_LIKE_PROPERTY_NAMES",
    "Boundary",
    "ContractError",
    "FixtureKind",
    "ValidationError",
    "boundary_of",
    "contracts_root",
    "data_property_names",
    "declared_property_names",
    "evaluation_private_property_names",
    "fixture_dir",
    "is_valid",
    "iter_errors",
    "iter_fixtures",
    "load_schema",
    "runner_facing_schema_names",
    "schema_dir",
    "schema_names",
    "validate",
    "validate_definition",
    "validator_for",
    "validator_for_definition",
]
