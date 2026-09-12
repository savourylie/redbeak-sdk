"""The shipped runner must not install or import a web framework."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import redbeak_runner

RUNNER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_RUNTIME_DEPS = {
    "redbeak-contracts",
    "redbeak-adapter-sdk",
    "httpx>=0.27",
    "typer>=0.12",
}


def test_runner_runtime_dependencies_are_the_customer_install() -> None:
    document = tomllib.loads((RUNNER_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert set(document["project"]["dependencies"]) == EXPECTED_RUNTIME_DEPS


def test_shipped_package_contains_no_reference_server() -> None:
    root = Path(redbeak_runner.__file__).resolve().parent
    assert not (root / "server").exists()
    assert "redbeak_runner.server" not in sys.modules


def test_cli_import_does_not_load_starlette() -> None:
    probe = """
import sys
import redbeak_runner.cli  # noqa: F401

loaded = [name for name in sys.modules if name == "starlette" or name.startswith("starlette.")]
assert not loaded, loaded
assert "uvicorn" not in sys.modules
assert "redbeak_reference_server" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
