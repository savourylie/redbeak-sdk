"""Local storage for the Project-scoped CLI token.

A CLI token is a control-plane credential: it creates and reads Runs in one
Project. It is deliberately not a runner key and cannot claim work, submit a
turn, complete a case, or send a heartbeat.

The value never travels as a command argument, because argument vectors are
visible to other local processes and are echoed by shell history. It is read
from the environment or standard input and stored in a mode-0600 file.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from redbeak_runner.errors import ConfigurationError

#: Same shape as a runner key, with its own prefix so the two cannot be confused.
TOKEN_PATTERN = "rbc_<16 hex>.<43 url-safe characters>"
DEFAULT_PATH = Path(".redbeak/cli-credentials.json")


def credentials_path() -> Path:
    configured = os.environ.get("REDBEAK_CLI_CREDENTIALS")
    return Path(configured) if configured else DEFAULT_PATH


@dataclass(frozen=True, slots=True)
class Credentials:
    base_url: str
    project_id: str
    token: str

    def __repr__(self) -> str:
        return (
            f"Credentials(base_url={self.base_url!r}, "
            f"project_id={self.project_id!r}, token='[redacted]')"
        )


def valid_token(value: str) -> bool:
    prefix, _, secret = value.partition(".")
    return (
        prefix.startswith("rbc_")
        and len(prefix) == 20
        and all(c in "0123456789abcdef" for c in prefix[4:])
        and len(secret) == 43
    )


def save(credentials: Credentials, *, path: Path | None = None) -> Path:
    """Write the credential file, creating its directory mode 0700."""

    destination = path or credentials_path()
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    document = {
        "baseUrl": credentials.base_url,
        "projectId": credentials.project_id,
        "token": credentials.token,
    }
    # Create with restrictive permissions rather than widening them afterwards.
    handle = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        json.dump(document, file, indent=2, sort_keys=True)
        file.write("\n")
    os.chmod(destination, 0o600)
    return destination


def clear(*, path: Path | None = None) -> bool:
    destination = path or credentials_path()
    if not destination.exists():
        return False
    destination.unlink()
    return True


def load(
    *,
    base_url: str | None = None,
    project_id: str | None = None,
    token: str | None = None,
    path: Path | None = None,
) -> Credentials:
    """Resolve credentials from explicit arguments, then env, then the file."""

    stored: dict[str, str] = {}
    destination = path or credentials_path()
    if destination.exists():
        mode = stat.S_IMODE(destination.stat().st_mode)
        if mode & 0o077:
            raise ConfigurationError(
                f"{destination} is readable by other users; run: chmod 600 {destination}"
            )
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"{destination} is not readable JSON") from exc
        if isinstance(loaded, dict):
            stored = {k: v for k, v in loaded.items() if isinstance(v, str)}
    resolved_url = base_url or os.environ.get("REDBEAK_API_BASE_URL") or stored.get("baseUrl", "")
    resolved_project = (
        project_id or os.environ.get("REDBEAK_PROJECT_ID") or stored.get("projectId", "")
    )
    resolved_token = token or os.environ.get("REDBEAK_CLI_TOKEN") or stored.get("token", "")
    missing = [
        label
        for label, value in (
            ("--base-url or REDBEAK_API_BASE_URL", resolved_url),
            ("--project or REDBEAK_PROJECT_ID", resolved_project),
            ("REDBEAK_CLI_TOKEN or redbeak auth login", resolved_token),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError("missing CLI configuration: " + ", ".join(missing))
    if not valid_token(resolved_token):
        raise ConfigurationError(f"CLI token is not shaped like {TOKEN_PATTERN}")
    return Credentials(
        base_url=resolved_url.rstrip("/"), project_id=resolved_project, token=resolved_token
    )
