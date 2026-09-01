"""Shared fixture discovery for the contract tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import redbeak_contracts as rc

VALID: list[tuple[str, Path, Any]] = list(rc.iter_fixtures("valid"))
INVALID: list[tuple[str, Path, Any]] = list(rc.iter_fixtures("invalid"))


def case_id(entry: tuple[str, Path, Any]) -> str:
    schema, path, _ = entry
    return f"{schema}/{path.stem}"
