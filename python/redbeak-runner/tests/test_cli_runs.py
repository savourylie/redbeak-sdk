"""TAI-200: local credential handling, wait mode, and the control-plane client.

These cover the runner-side half of local Run creation. The control-plane half —
authorization, version pinning, idempotency and Run-scoped claiming — is verified
against real PostgreSQL in packages/control-plane/test/integration/cli-runs.test.ts.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from _runner_fixtures import CASE_A, PROJECT_ID, RUN_ID, NoteAdapter, two_turn_case
from redbeak_runner import credentials as creds
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.cli import app
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig
from redbeak_runner.control_plane import ControlPlaneClient, ControlPlaneError
from redbeak_runner.errors import ConfigurationError
from redbeak_runner.loop import _next_backoff, run_until_idle
from redbeak_runner.server import ReferenceServer, create_app
from typer.testing import CliRunner

runner = CliRunner()
TOKEN = "rbc_0123456789abcdef." + "A" * 43
RUNNER_KEY = "rbk_0123456789abcdef." + "B" * 43


def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "REDBEAK_CLI_TOKEN",
        "REDBEAK_API_BASE_URL",
        "REDBEAK_PROJECT_ID",
        "REDBEAK_CLI_CREDENTIALS",
    ):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------
# Local credential file
# --------------------------------------------------------------------------


def test_saved_credentials_are_private_and_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    path = tmp_path / "nested" / "cli-credentials.json"
    saved = creds.save(
        creds.Credentials(base_url="http://127.0.0.1:3000", project_id=PROJECT_ID, token=TOKEN),
        path=path,
    )
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    loaded = creds.load(path=path)
    assert loaded.project_id == PROJECT_ID
    assert loaded.token == TOKEN
    # The token is never rendered, including in a traceback or log line.
    assert TOKEN not in repr(loaded)
    assert "[redacted]" in repr(loaded)


def test_world_readable_credentials_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    path = tmp_path / "cli-credentials.json"
    creds.save(
        creds.Credentials(base_url="http://127.0.0.1:3000", project_id=PROJECT_ID, token=TOKEN),
        path=path,
    )
    os.chmod(path, 0o644)
    with pytest.raises(ConfigurationError) as caught:
        creds.load(path=path)
    assert "chmod 600" in str(caught.value)


def test_environment_overrides_the_stored_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    path = tmp_path / "cli-credentials.json"
    creds.save(
        creds.Credentials(base_url="http://127.0.0.1:1", project_id=PROJECT_ID, token=TOKEN),
        path=path,
    )
    other = "rbc_fedcba9876543210." + "C" * 43
    monkeypatch.setenv("REDBEAK_CLI_TOKEN", other)
    monkeypatch.setenv("REDBEAK_API_BASE_URL", "http://127.0.0.1:3000/")
    loaded = creds.load(path=path)
    assert loaded.token == other
    # A trailing slash would otherwise produce a double slash in every path.
    assert loaded.base_url == "http://127.0.0.1:3000"


def test_a_runner_key_is_not_accepted_as_a_cli_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    assert creds.valid_token(TOKEN)
    assert not creds.valid_token(RUNNER_KEY)
    assert not creds.valid_token("rbs_" + "D" * 43)
    monkeypatch.setenv("REDBEAK_CLI_TOKEN", RUNNER_KEY)
    with pytest.raises(ConfigurationError):
        creds.load(
            base_url="http://127.0.0.1:3000", project_id=PROJECT_ID, path=tmp_path / "absent.json"
        )


def test_missing_configuration_names_every_missing_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    with pytest.raises(ConfigurationError) as caught:
        creds.load(path=tmp_path / "absent.json")
    message = str(caught.value)
    assert "REDBEAK_API_BASE_URL" in message
    assert "REDBEAK_PROJECT_ID" in message
    assert "redbeak auth login" in message


# --------------------------------------------------------------------------
# Bounded wait-mode backoff
# --------------------------------------------------------------------------


def test_backoff_is_bounded_and_honours_the_server_hint() -> None:
    config = RunnerConfig(
        runner_id="r",
        adapter_spec="",
        runner_key="",
        base_url="",
        checkpoint_dir=Path("."),
        artifacts_dir=Path("."),
        poll_min_s=1.0,
        poll_max_s=8.0,
    )
    # Doubling, never past the ceiling.
    assert _next_backoff(1.0, None, config) == 2.0
    assert _next_backoff(4.0, None, config) == 8.0
    assert _next_backoff(8.0, None, config) == 8.0
    # A server hint is honoured, still inside the configured bounds.
    assert _next_backoff(1.0, 5000, config) == 5.0
    assert _next_backoff(1.0, 999_999, config) == 8.0
    # A nonsensical hint falls back to the floor rather than busy-looping.
    assert _next_backoff(1.0, -1, config) == 2.0
    assert _next_backoff(1.0, "soon", config) == 2.0


# --------------------------------------------------------------------------
# Wait mode against the reference protocol implementation
# --------------------------------------------------------------------------


def _config(tmp_path: Path, **changes: Any) -> RunnerConfig:
    base = {
        "runner_id": "77777777-7777-4777-8777-777777777777",
        "adapter_spec": "",
        "runner_key": "",
        "base_url": "http://redbeak.local",
        "checkpoint_dir": tmp_path / "ckpt",
        "artifacts_dir": tmp_path / "art",
        "heartbeat_interval_s": 0.0,
        "until_idle": True,
    }
    base.update(changes)
    return RunnerConfig(**base)  # type: ignore[arg-type]


def test_an_idle_waiting_runner_accepts_work_queued_later(tmp_path: Path) -> None:
    """AC 4: a runner already waiting picks up a Run created after it started."""

    server = ReferenceServer(retry_after_ms=10)
    key = server.create_key(PROJECT_ID)
    adapter = NoteAdapter()

    async def scenario() -> Any:
        transport = httpx.ASGITransport(app=create_app(server))
        async with httpx.AsyncClient(transport=transport, base_url="http://redbeak.local") as http:
            stop = asyncio.Event()
            config = _config(tmp_path, wait=True, poll_min_s=0.01, poll_max_s=0.05)
            task = asyncio.create_task(
                run_until_idle(
                    config=config,
                    adapter=adapter,
                    client=RunnerClient(http, runner_key=key),
                    artifacts=ArtifactStore(
                        config.artifacts_dir, created_at="2026-09-01T00:00:00Z"
                    ),
                    stop=stop,
                )
            )
            # The queue is empty; the runner must still be alive a moment later.
            await asyncio.sleep(0.15)
            assert not task.done()
            server.enqueue(two_turn_case())
            for _ in range(200):
                await asyncio.sleep(0.02)
                if adapter.sent:
                    break
            # Executed the newly queued case, then went back to waiting.
            await asyncio.sleep(0.1)
            assert not task.done()
            stop.set()
            return await asyncio.wait_for(task, timeout=5)

    result = asyncio.run(scenario())
    assert result.stopped is True
    assert [case.case_execution_id for case in result.cases] == [CASE_A]
    assert result.run_ids == [RUN_ID]
    assert adapter.sent == ["hello", "again"]


def test_wait_mode_stops_on_request_without_claiming_more_work(tmp_path: Path) -> None:
    """AC 4: a stop request ends waiting promptly and leaves the queue intact."""

    server = ReferenceServer(retry_after_ms=10)
    key = server.create_key(PROJECT_ID)
    adapter = NoteAdapter()

    async def scenario() -> Any:
        transport = httpx.ASGITransport(app=create_app(server))
        async with httpx.AsyncClient(transport=transport, base_url="http://redbeak.local") as http:
            stop = asyncio.Event()
            config = _config(tmp_path, wait=True, poll_min_s=0.5, poll_max_s=0.5)
            task = asyncio.create_task(
                run_until_idle(
                    config=config,
                    adapter=adapter,
                    client=RunnerClient(http, runner_key=key),
                    artifacts=ArtifactStore(
                        config.artifacts_dir, created_at="2026-09-01T00:00:00Z"
                    ),
                    stop=stop,
                )
            )
            await asyncio.sleep(0.05)
            stop.set()
            # Work queued after the stop must not be claimed.
            server.enqueue(two_turn_case())
            return await asyncio.wait_for(task, timeout=5)

    result = asyncio.run(scenario())
    assert result.stopped is True
    assert result.cases == []
    assert adapter.sent == []


def test_without_wait_the_runner_still_exits_when_idle(tmp_path: Path) -> None:
    """Existing --until-idle behaviour is unchanged by wait mode."""

    server = ReferenceServer(retry_after_ms=10)
    key = server.create_key(PROJECT_ID)

    async def scenario() -> Any:
        transport = httpx.ASGITransport(app=create_app(server))
        async with httpx.AsyncClient(transport=transport, base_url="http://redbeak.local") as http:
            config = _config(tmp_path)
            return await run_until_idle(
                config=config,
                adapter=NoteAdapter(),
                client=RunnerClient(http, runner_key=key),
                artifacts=ArtifactStore(config.artifacts_dir, created_at="2026-09-01T00:00:00Z"),
            )

    result = asyncio.wait_for(scenario(), timeout=5)
    outcome = asyncio.run(result)
    assert outcome.cases == []
    assert outcome.stopped is False


# --------------------------------------------------------------------------
# Control-plane client
# --------------------------------------------------------------------------


def _client(handler: Any) -> tuple[ControlPlaneClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:3000"
    )
    return ControlPlaneClient(http, token=TOKEN, project_id=PROJECT_ID), http


def test_create_run_targets_the_cli_surface_and_carries_the_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"schemaVersion": "0.1", "runId": RUN_ID})

    async def scenario() -> Any:
        client, http = _client(handler)
        async with http:
            return await client.create_run({"attemptId": "a", "suite": "s"})

    payload = asyncio.run(scenario())
    assert payload["runId"] == RUN_ID
    # The CLI surface is separate from the frozen /v1 runner protocol.
    assert seen["url"] == f"http://127.0.0.1:3000/cli/v1/projects/{PROJECT_ID}/runs"
    assert "/v1/runner/" not in seen["url"]
    assert seen["auth"] == f"Bearer {TOKEN}"
    # The credential travels in the header, never in the body.
    assert TOKEN not in json.dumps(seen["body"])


def test_rejections_become_actionable_messages_without_echoing_the_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"schemaVersion": "0.1", "code": "unauthorized", "requestId": "r"}
        )

    async def scenario() -> None:
        client, http = _client(handler)
        async with http:
            await client.versions()

    with pytest.raises(ControlPlaneError) as caught:
        asyncio.run(scenario())
    assert caught.value.code == "unauthorized"
    assert caught.value.status_code == 401
    assert "redbeak auth login" in str(caught.value)
    assert TOKEN not in str(caught.value)


def test_an_unknown_code_is_reported_without_inventing_a_cause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"code": "something_new"})

    async def scenario() -> None:
        client, http = _client(handler)
        async with http:
            await client.run_status(RUN_ID)

    with pytest.raises(ControlPlaneError) as caught:
        asyncio.run(scenario())
    assert "HTTP 500" in str(caught.value)
    assert caught.value.code == "something_new"


# --------------------------------------------------------------------------
# Command surface
# --------------------------------------------------------------------------


def test_create_requires_a_selector_and_an_adapter_for_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("REDBEAK_CLI_CREDENTIALS", str(tmp_path / "absent.json"))
    missing_suite = runner.invoke(app, ["runs", "create", "--target", "t"])
    assert missing_suite.exit_code == 1
    assert "--suite" in missing_suite.output
    missing_target = runner.invoke(app, ["runs", "create", "--suite", "s"])
    assert missing_target.exit_code == 1
    assert "--target" in missing_target.output
    needs_adapter = runner.invoke(
        app, ["runs", "create", "--suite", "s", "--target", "t", "--execute"]
    )
    assert needs_adapter.exit_code == 1
    assert "--adapter" in needs_adapter.output


def test_create_reports_missing_configuration_instead_of_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    monkeypatch.setenv("REDBEAK_CLI_CREDENTIALS", str(tmp_path / "absent.json"))
    result = runner.invoke(app, ["runs", "create", "--suite", "s", "--target", "t"])
    assert result.exit_code == 1
    assert "missing CLI configuration" in result.output
    assert "Traceback" not in result.output


def test_logout_removes_only_the_local_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    path = tmp_path / "cli-credentials.json"
    monkeypatch.setenv("REDBEAK_CLI_CREDENTIALS", str(path))
    creds.save(
        creds.Credentials(base_url="http://127.0.0.1:3000", project_id=PROJECT_ID, token=TOKEN)
    )
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert not path.exists()
    # Deleting a local file is not revocation, and the CLI says so.
    assert "revoke the token" in result.output
    assert TOKEN not in result.output
    again = runner.invoke(app, ["auth", "logout"])
    assert again.exit_code == 0
    assert "no stored credentials" in again.output


def test_login_refuses_a_value_that_is_not_a_cli_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean_env(monkeypatch)
    path = tmp_path / "cli-credentials.json"
    monkeypatch.setenv("REDBEAK_CLI_CREDENTIALS", str(path))
    monkeypatch.setenv("REDBEAK_CLI_TOKEN", RUNNER_KEY)
    result = runner.invoke(
        app,
        ["auth", "login", "--base-url", "http://127.0.0.1:3000", "--project", PROJECT_ID],
    )
    assert result.exit_code == 1
    assert "rbc_" in result.output
    # Nothing is stored when verification cannot even be attempted.
    assert not path.exists()
