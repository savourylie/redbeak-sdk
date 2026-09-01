"""Typer CLI: ``redbeak runner start`` and ``redbeak runner doctor``."""

# Typer's Option() defaults are the documented CLI surface; B008 is a false
# positive against that pattern.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import typer

from redbeak_runner.adapters import load_adapter
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig, require_key, require_runner_id
from redbeak_runner.errors import ConfigurationError, RunnerError
from redbeak_runner.log import configure_logging, get_logger
from redbeak_runner.loop import run_until_idle
from redbeak_runner.server import ReferenceServer, create_app, load_work_file

app = typer.Typer(no_args_is_help=True, add_completion=False)
runner_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(runner_app, name="runner")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@runner_app.command("doctor")
def doctor(
    adapter: str = typer.Option(..., "--adapter", help="Import path of the target adapter."),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Remote runner API. Ignored with --reference-server."
    ),
    runner_key: str | None = typer.Option(
        None, "--runner-key", envvar="REDBEAK_RUNNER_KEY", help="Project-scoped runner key."
    ),
    reference_server: bool = typer.Option(
        False, "--reference-server", help="Check against an in-memory OpenAPI server."
    ),
) -> None:
    """Validate adapter discovery, contract version, and authentication shape."""

    configure_logging()
    log = get_logger()
    loaded = load_adapter(adapter)
    capabilities = asyncio.run(loaded.capabilities())
    summary = (
        f"adapter ok name={capabilities.adapter_name} "
        f"version={capabilities.adapter_version} contract={capabilities.contract_version}"
    )
    typer.echo(summary)
    log.info("%s", summary)
    if reference_server:
        server = ReferenceServer()
        key = runner_key or server.create_key("11111111-1111-4111-8111-111111111111")
        http_app = create_app(server)
        payload = asyncio.run(_doctor_claim(http_app, key, capabilities.adapter_version))
        typer.echo(f"reference server ok status={payload['status']}")
        log.info("reference server ok status=%s", payload["status"])
        return
    resolved_url = base_url or os.environ.get("REDBEAK_API_BASE_URL")
    if resolved_url:
        require_key(runner_key, allow_missing=False)
        typer.echo("remote base_url configured")
        log.info("remote base_url configured")
        return
    typer.echo("no remote base_url; adapter-only doctor")
    log.info("no remote base_url; adapter-only doctor")


@runner_app.command("start")
def start(
    adapter: str = typer.Option(..., "--adapter", help="Import path of the target adapter."),
    base_url: str | None = typer.Option(
        None, "--base-url", help="Remote runner API. Ignored with --reference-server."
    ),
    runner_key: str | None = typer.Option(
        None, "--runner-key", envvar="REDBEAK_RUNNER_KEY", help="Project-scoped runner key."
    ),
    runner_id: str | None = typer.Option(None, "--runner-id", help="Stable runner UUID."),
    checkpoint_dir: Path = typer.Option(
        Path(".redbeak-runner"), "--checkpoint-dir", help="Non-secret local checkpoint directory."
    ),
    artifacts_dir: Path = typer.Option(
        Path("artifacts/runner"), "--artifacts-dir", help="Item-level raw evidence directory."
    ),
    reference_server: bool = typer.Option(
        False, "--reference-server", help="Serve the OpenAPI contract in-process."
    ),
    work_file: Path | None = typer.Option(
        None, "--work-file", help="Queued cases for the reference server."
    ),
    until_idle: bool = typer.Option(
        True,
        "--until-idle/--once",
        help="Drain the queue (default). --once claims and executes a single case.",
    ),
    heartbeat_interval_s: float = typer.Option(5.0, "--heartbeat-interval"),
) -> None:
    """Claim work, invoke the adapter, and submit idempotent evidence."""

    configure_logging()
    log = get_logger()
    try:
        result = asyncio.run(
            _start(
                adapter_spec=adapter,
                base_url=base_url,
                runner_key=runner_key,
                runner_id=runner_id,
                checkpoint_dir=checkpoint_dir,
                artifacts_dir=artifacts_dir,
                reference_server=reference_server,
                work_file=work_file,
                until_idle=until_idle,
                heartbeat_interval_s=heartbeat_interval_s,
            )
        )
    except (ConfigurationError, RunnerError) as exc:
        log.info("runner failed code=%s", exc.code)
        raise typer.Exit(code=1) from exc
    typer.echo(f"runner finished cases={len(result.cases)}")
    log.info("runner finished cases=%s", len(result.cases))
    for case in result.cases:
        line = (
            f"case case_execution_id={case.case_execution_id} "
            f"status={case.status} turns={len(case.sequences)}"
        )
        typer.echo(line)
        log.info("%s", line)


async def _doctor_claim(http_app: Any, key: str, adapter_version: str) -> dict[str, Any]:
    from redbeak_runner import CONTRACT_VERSION, RUNNER_VERSION
    from redbeak_runner.ids import new_uuid

    transport = httpx.ASGITransport(app=http_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://redbeak.local") as http:
        client = RunnerClient(http, runner_key=key)
        return await client.claim(
            {
                "schema_version": CONTRACT_VERSION,
                "runner_id": new_uuid(),
                "runner_version": RUNNER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "adapter_version": adapter_version,
                "capabilities": {"max_concurrent_cases": 1},
            }
        )


async def _start(
    *,
    adapter_spec: str,
    base_url: str | None,
    runner_key: str | None,
    runner_id: str | None,
    checkpoint_dir: Path,
    artifacts_dir: Path,
    reference_server: bool,
    work_file: Path | None,
    until_idle: bool,
    heartbeat_interval_s: float,
) -> Any:
    adapter = load_adapter(adapter_spec)
    if reference_server:
        server = ReferenceServer()
        work = load_work_file(work_file) if work_file is not None else None
        if work is not None:
            server.load_work(work)
            project_id = work.project_id
        else:
            project_id = "11111111-1111-4111-8111-111111111111"
        key = runner_key or server.create_key(project_id)
        http_app = create_app(server)
        transport = httpx.ASGITransport(app=http_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://redbeak.local") as http:
            return await _run_with_client(
                adapter=adapter,
                http=http,
                runner_key=key,
                runner_id=runner_id,
                checkpoint_dir=checkpoint_dir,
                artifacts_dir=artifacts_dir,
                until_idle=until_idle,
                heartbeat_interval_s=heartbeat_interval_s,
            )

    resolved_url = base_url or os.environ.get("REDBEAK_API_BASE_URL")
    if not resolved_url:
        raise ConfigurationError("pass --base-url or --reference-server")
    key = require_key(runner_key, allow_missing=False)
    async with httpx.AsyncClient(base_url=resolved_url) as http:
        return await _run_with_client(
            adapter=adapter,
            http=http,
            runner_key=key,
            runner_id=runner_id,
            checkpoint_dir=checkpoint_dir,
            artifacts_dir=artifacts_dir,
            until_idle=until_idle,
            heartbeat_interval_s=heartbeat_interval_s,
        )


async def _run_with_client(
    *,
    adapter: Any,
    http: httpx.AsyncClient,
    runner_key: str,
    runner_id: str | None,
    checkpoint_dir: Path,
    artifacts_dir: Path,
    until_idle: bool,
    heartbeat_interval_s: float,
) -> Any:
    config = RunnerConfig(
        runner_id=require_runner_id(runner_id),
        adapter_spec="",
        runner_key=runner_key,
        base_url=str(http.base_url),
        checkpoint_dir=checkpoint_dir,
        artifacts_dir=artifacts_dir,
        heartbeat_interval_s=heartbeat_interval_s,
        until_idle=until_idle,
    )
    client = RunnerClient(http, runner_key=runner_key)
    store = ArtifactStore(artifacts_dir, created_at=_now())
    return await run_until_idle(config=config, adapter=adapter, client=client, artifacts=store)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
