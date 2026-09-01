"""Runner configuration. Secrets live in env/CLI, never in the checkpoint."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from redbeak_runner import RUNNER_VERSION
from redbeak_runner.errors import ConfigurationError
from redbeak_runner.ids import new_uuid


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    runner_id: str
    adapter_spec: str
    runner_key: str
    base_url: str
    checkpoint_dir: Path
    artifacts_dir: Path
    runner_version: str = RUNNER_VERSION
    heartbeat_interval_s: float = 5.0
    max_retries: int = 3
    until_idle: bool = False

    def __repr__(self) -> str:
        return (
            "RunnerConfig("
            f"runner_id={self.runner_id!r}, adapter_spec={self.adapter_spec!r}, "
            f"runner_key='[redacted]', base_url={self.base_url!r}, "
            f"checkpoint_dir={str(self.checkpoint_dir)!r})"
        )


def require_runner_id(value: str | None) -> str:
    if value:
        return value
    return new_uuid()


def require_key(value: str | None, *, allow_missing: bool) -> str:
    if value:
        return value
    if allow_missing:
        return ""
    raise ConfigurationError("REDBEAK_RUNNER_KEY is required when talking to a remote server")
