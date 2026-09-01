"""The runner package must stay generic: no BFCL-specific code."""

from __future__ import annotations

from pathlib import Path

import redbeak_runner

FORBIDDEN = ("bfcl", "gorilla", "trading_bot", "travel_booking", "vehicle_control")


def test_runner_sources_contain_no_bfcl_specific_code() -> None:
    root = Path(redbeak_runner.__file__).resolve().parent
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for needle in FORBIDDEN:
            if needle in text:
                offenders.append(f"{path.name}:{needle}")
    assert offenders == []
