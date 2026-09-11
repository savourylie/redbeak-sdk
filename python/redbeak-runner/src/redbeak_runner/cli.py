"""Typer CLI.

``redbeak runner`` executes queued work. ``redbeak auth`` and ``redbeak runs``
are the local Run-creation path added by TAI-200: they talk to the control-plane
surface `cli 0.1`, not to the frozen runner protocol, and they authenticate with
a Project-scoped CLI token rather than a runner key.

The CLI never scores anything. It submits raw evidence and points at Redbeak,
where analysis is published.
"""

# Typer's Option() defaults are the documented CLI surface; B008 is a false
# positive against that pattern.
# ruff: noqa: B008

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import typer

from redbeak_runner import credentials as creds
from redbeak_runner.adapters import load_adapter
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig, require_key, require_runner_id
from redbeak_runner.control_plane import ControlPlaneClient
from redbeak_runner.errors import ConfigurationError, RunnerError
from redbeak_runner.log import configure_logging, get_logger
from redbeak_runner.loop import run_until_idle

app = typer.Typer(no_args_is_help=True, add_completion=False)
runner_app = typer.Typer(no_args_is_help=True, add_completion=False)
auth_app = typer.Typer(no_args_is_help=True, add_completion=False)
runs_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(runner_app, name="runner")
app.add_typer(auth_app, name="auth", help="Manage the local Project-scoped CLI token.")
app.add_typer(runs_app, name="runs", help="Create and inspect Runs in a Redbeak Project.")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reference_server() -> Any:
    """Load the in-memory OpenAPI stand-in used by repository tests.

    The shipped runner does not include this package. Customers talk to a
    Redbeak Project over outbound HTTP.
    """

    try:
        import redbeak_reference_server as reference
    except ImportError as exc:
        raise ConfigurationError(
            "the in-memory reference server is a repository test tool; "
            "pass --base-url for a Redbeak Project"
        ) from exc
    return reference


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
        False,
        "--reference-server",
        help="Repository-test stand-in. Requires redbeak-reference-server.",
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
        try:
            reference = _reference_server()
        except ConfigurationError as exc:
            typer.echo(_reason(exc), err=True)
            raise typer.Exit(code=1) from exc
        server = reference.ReferenceServer()
        key = runner_key or server.create_key("11111111-1111-4111-8111-111111111111")
        http_app = reference.create_app(server)
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
        False,
        "--reference-server",
        help="Repository-test stand-in. Requires redbeak-reference-server.",
    ),
    work_file: Path | None = typer.Option(
        None, "--work-file", help="Queued cases for the repository-test stand-in."
    ),
    until_idle: bool = typer.Option(
        True,
        "--until-idle/--once",
        help="Drain the queue (default). --once claims and executes a single case.",
    ),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Stay running when the queue is empty and accept a Run created later.",
    ),
    poll_min_s: float = typer.Option(
        1.0, "--poll-min", help="Shortest wait-mode backoff, seconds."
    ),
    poll_max_s: float = typer.Option(
        30.0, "--poll-max", help="Longest wait-mode backoff, seconds."
    ),
    heartbeat_interval_s: float = typer.Option(5.0, "--heartbeat-interval"),
) -> None:
    """Claim work, invoke the adapter, and submit idempotent evidence.

    Default `--until-idle` drains every compatible queued case and exits when the
    server reports no work. `--once` executes a single case. `--wait` keeps the
    process alive after an idle response with bounded backoff, so a Run created
    later in the dashboard is picked up without restarting; Ctrl-C or SIGTERM
    stops it between cases without abandoning one already leased.
    """

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
                wait=wait,
                poll_min_s=poll_min_s,
                poll_max_s=poll_max_s,
            )
        )
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        log.info("runner failed code=%s", exc.code)
        raise typer.Exit(code=1) from exc
    if result.stopped:
        typer.echo("runner stopped on request")
        log.info("runner stopped on request")
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
    wait: bool = False,
    poll_min_s: float = 1.0,
    poll_max_s: float = 30.0,
) -> Any:
    adapter = load_adapter(adapter_spec)
    if reference_server:
        reference = _reference_server()
        server = reference.ReferenceServer()
        work = reference.load_work_file(work_file) if work_file is not None else None
        if work is not None:
            server.load_work(work)
            project_id = work.project_id
        else:
            project_id = "11111111-1111-4111-8111-111111111111"
        key = runner_key or server.create_key(project_id)
        http_app = reference.create_app(server)
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
                wait=wait,
                poll_min_s=poll_min_s,
                poll_max_s=poll_max_s,
            )

    resolved_url = base_url or os.environ.get("REDBEAK_API_BASE_URL")
    if not resolved_url:
        raise ConfigurationError("pass --base-url or --reference-server")
    key = require_key(runner_key, allow_missing=False)
    async with httpx.AsyncClient(base_url=resolved_url, timeout=_TIMEOUT) as http:
        return await _run_with_client(
            adapter=adapter,
            http=http,
            runner_key=key,
            runner_id=runner_id,
            checkpoint_dir=checkpoint_dir,
            artifacts_dir=artifacts_dir,
            until_idle=until_idle,
            heartbeat_interval_s=heartbeat_interval_s,
            wait=wait,
            poll_min_s=poll_min_s,
            poll_max_s=poll_max_s,
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
    wait: bool = False,
    poll_min_s: float = 1.0,
    poll_max_s: float = 30.0,
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
        wait=wait,
        poll_min_s=poll_min_s,
        poll_max_s=poll_max_s,
    )
    client = RunnerClient(http, runner_key=runner_key)
    store = ArtifactStore(artifacts_dir, created_at=_now())
    async with _graceful_stop() as stop:
        return await run_until_idle(
            config=config, adapter=adapter, client=client, artifacts=store, stop=stop
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()


# --------------------------------------------------------------------------
# Local Run creation (TAI-200)
# --------------------------------------------------------------------------

#: Generous enough for a local model turn, bounded so a hung server is visible.
_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)


def _reason(exc: Exception) -> str:
    """One actionable line. Never echo a credential or a server error body."""

    message = str(exc).strip()
    code = getattr(exc, "code", "")
    if message and code:
        return f"error: {message} ({code})"
    return f"error: {message or code or exc.__class__.__name__}"


@contextlib.asynccontextmanager
async def _graceful_stop() -> Any:
    """Turn SIGINT/SIGTERM into a request to stop between cases.

    A leased case is never abandoned mid-turn: the signal sets an event that the
    claim loop checks between cases and during a backoff sleep. A second signal
    restores default handling so an unresponsive process can still be killed.
    """

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed: list[Any] = []
    for name in ("SIGINT", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is None:
            continue

        def request_stop(number: Any = number) -> None:
            stop.set()
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(number)
            typer.echo("stop requested; finishing the current case", err=True)

        try:
            loop.add_signal_handler(number, request_stop)
        except (NotImplementedError, RuntimeError, ValueError):  # pragma: no cover - platform
            continue
        installed.append(number)
    try:
        yield stop
    finally:
        for number in installed:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(number)


def _client(credentials: creds.Credentials, http: httpx.AsyncClient) -> ControlPlaneClient:
    return ControlPlaneClient(http, token=credentials.token, project_id=credentials.project_id)


def _resolve(base_url: str | None, project: str | None) -> creds.Credentials:
    return creds.load(base_url=base_url, project_id=project)


@auth_app.command("login")
def auth_login(
    base_url: str = typer.Option(..., "--base-url", help="Redbeak application origin."),
    project: str = typer.Option(..., "--project", help="Project UUID from the dashboard URL."),
    token_stdin: bool = typer.Option(
        False, "--token-stdin", help="Read the CLI token from standard input instead of prompting."
    ),
) -> None:
    """Store a Project-scoped CLI token locally, after verifying it works.

    The token is read from REDBEAK_CLI_TOKEN, standard input, or a hidden prompt.
    It is deliberately not accepted as a command argument: argument vectors are
    visible to other local processes and are recorded in shell history.
    """

    configure_logging()
    environment = os.environ.get("REDBEAK_CLI_TOKEN", "")
    if environment:
        token = environment
    elif token_stdin:
        token = sys.stdin.readline().strip()
    else:
        token = typer.prompt("CLI token", hide_input=True).strip()
    if not creds.valid_token(token):
        typer.echo(f"error: a CLI token looks like {creds.TOKEN_PATTERN}", err=True)
        raise typer.Exit(code=1)
    candidate = creds.Credentials(base_url=base_url.rstrip("/"), project_id=project, token=token)
    try:
        catalogue = asyncio.run(_verify(candidate))
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        raise typer.Exit(code=1) from exc
    destination = creds.save(candidate)
    typer.echo(f"signed in project={project} credentials={destination}")
    typer.echo(
        f"suites={len(catalogue.get('suites', []))} targets={len(catalogue.get('targets', []))}"
    )


async def _verify(credentials: creds.Credentials) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=credentials.base_url, timeout=_TIMEOUT) as http:
        return await _client(credentials, http).versions()


@auth_app.command("status")
def auth_status(
    base_url: str | None = typer.Option(None, "--base-url"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    """Report where the CLI would send Runs, and whether the token still works."""

    configure_logging()
    try:
        credentials = _resolve(base_url, project)
        asyncio.run(_verify(credentials))
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"active base_url={credentials.base_url} project={credentials.project_id} "
        f"credentials={creds.credentials_path()}"
    )


@auth_app.command("logout")
def auth_logout() -> None:
    """Delete the stored CLI token. It remains valid until revoked in Redbeak."""

    configure_logging()
    path = creds.credentials_path()
    if creds.clear():
        typer.echo(f"removed {path}")
        typer.echo("revoke the token in Project settings to end its access")
        return
    typer.echo(f"no stored credentials at {path}")


@runs_app.command("versions")
def runs_versions(
    base_url: str | None = typer.Option(None, "--base-url"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    """List the immutable SuiteVersions and TargetVersions a Run can pin."""

    configure_logging()
    try:
        credentials = _resolve(base_url, project)
        catalogue = asyncio.run(_verify(credentials))
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        raise typer.Exit(code=1) from exc
    for suite in catalogue.get("suites", []):
        typer.echo(
            f"suite slug={suite['slug']} version={suite['version']} "
            f"cases={suite['caseCount']} id={suite['id']}"
        )
    for target in catalogue.get("targets", []):
        typer.echo(
            f"target name={target['name']!r} version={target['version']} "
            f"adapter_version={target['adapterVersion']} id={target['id']}"
        )
    if not catalogue.get("suites") or not catalogue.get("targets"):
        typer.echo("seed evaluation data and register a target before creating a Run", err=True)


@runs_app.command("status")
def runs_status(
    run_id: str = typer.Argument(..., help="Run UUID."),
    base_url: str | None = typer.Option(None, "--base-url"),
    project: str | None = typer.Option(None, "--project"),
) -> None:
    """Report a Run's lifecycle state and where to read its analysis."""

    configure_logging()
    try:
        credentials = _resolve(base_url, project)
        status = asyncio.run(_status(credentials, run_id))
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        raise typer.Exit(code=1) from exc
    _report_status(status)


async def _status(credentials: creds.Credentials, run_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=credentials.base_url, timeout=_TIMEOUT) as http:
        return await _client(credentials, http).run_status(run_id)


#: Run state to what a person should do next. Execution and analysis are
#: deliberately distinct: submitted raw evidence is not an evaluated result.
_STATE_MEANING = {
    "queued": "queued; no runner has claimed a case yet",
    "running": "executing; a runner is submitting raw evidence",
    "evaluating": "execution submitted; Redbeak is analysing it",
    "completed": "analysis published in Redbeak",
    "failed": "no case produced analysable evidence; open the Run for the reason",
    "canceled": "canceled",
}


def _report_status(status: dict[str, Any]) -> None:
    state = str(status.get("state", "unknown"))
    typer.echo(f"run run_id={status.get('id')} state={state} — {_STATE_MEANING.get(state, state)}")
    progress = status.get("progress")
    if isinstance(progress, dict):
        states = progress.get("states")
        rendered = (
            " ".join(f"{k}={v}" for k, v in sorted(states.items()))
            if isinstance(states, dict)
            else ""
        )
        typer.echo(f"cases total={progress.get('total')} {rendered}".rstrip())
    typer.echo(f"dashboard {status.get('url')}")
    typer.echo("Redbeak publishes the analysis; the runner never scores locally.")


@runs_app.command("create")
def runs_create(
    suite: str | None = typer.Option(
        None, "--suite", help="Suite slug, as listed by 'runs versions'."
    ),
    suite_version: str | None = typer.Option(None, "--suite-version", help="Suite version pin."),
    suite_version_id: str | None = typer.Option(None, "--suite-version-id", help="Exact UUID."),
    target: str | None = typer.Option(None, "--target", help="Registered Target name."),
    target_version: str | None = typer.Option(None, "--target-version", help="Target version pin."),
    target_version_id: str | None = typer.Option(None, "--target-version-id", help="Exact UUID."),
    attempt_id: str | None = typer.Option(
        None,
        "--attempt-id",
        help="Reuse an attempt UUID to retry safely after a lost response.",
    ),
    base_url: str | None = typer.Option(None, "--base-url"),
    project: str | None = typer.Option(None, "--project"),
    execute: bool = typer.Option(
        False, "--execute", help="Execute this Run locally after creating it."
    ),
    adapter: str | None = typer.Option(None, "--adapter", help="Adapter to execute with."),
    runner_id: str | None = typer.Option(None, "--runner-id"),
    checkpoint_dir: Path = typer.Option(Path(".redbeak-runner"), "--checkpoint-dir"),
    artifacts_dir: Path = typer.Option(Path("artifacts/runner"), "--artifacts-dir"),
    heartbeat_interval_s: float = typer.Option(5.0, "--heartbeat-interval"),
) -> None:
    """Create a Run in Redbeak, and optionally execute it here.

    Version pins are immutable: a Run records the exact SuiteVersion and
    TargetVersion it was created with. `--attempt-id` makes a retry after a lost
    response return the original Run instead of creating a second one.

    With `--execute`, Redbeak issues a short-lived credential bound to this Run,
    so the local runner can only claim this Run's cases and cannot drain other
    queued work. The grant is released when the command finishes.
    """

    configure_logging()
    log = get_logger()
    if execute and not adapter:
        typer.echo("error: --execute needs --adapter", err=True)
        raise typer.Exit(code=1)
    if not (suite or suite_version_id):
        typer.echo("error: pass --suite or --suite-version-id", err=True)
        raise typer.Exit(code=1)
    if not (target or target_version_id):
        typer.echo("error: pass --target or --target-version-id", err=True)
        raise typer.Exit(code=1)
    attempt = attempt_id or str(uuid.uuid4())
    request: dict[str, Any] = {"attemptId": attempt, "execute": execute}
    for key, value in (
        ("suite", suite),
        ("suiteVersion", suite_version),
        ("suiteVersionId", suite_version_id),
        ("target", target),
        ("targetVersion", target_version),
        ("targetVersionId", target_version_id),
    ):
        if value:
            request[key] = value
    try:
        credentials = _resolve(base_url, project)
        exit_code = asyncio.run(
            _create_and_execute(
                credentials=credentials,
                request=request,
                attempt=attempt,
                execute=execute,
                adapter_spec=adapter,
                runner_id=runner_id,
                checkpoint_dir=checkpoint_dir,
                artifacts_dir=artifacts_dir,
                heartbeat_interval_s=heartbeat_interval_s,
            )
        )
    except (ConfigurationError, RunnerError) as exc:
        typer.echo(_reason(exc), err=True)
        log.info("run create failed code=%s", getattr(exc, "code", "unknown"))
        raise typer.Exit(code=1) from exc
    if exit_code:
        raise typer.Exit(code=exit_code)


async def _create_and_execute(
    *,
    credentials: creds.Credentials,
    request: dict[str, Any],
    attempt: str,
    execute: bool,
    adapter_spec: str | None,
    runner_id: str | None,
    checkpoint_dir: Path,
    artifacts_dir: Path,
    heartbeat_interval_s: float,
) -> int:
    log = get_logger()
    async with httpx.AsyncClient(base_url=credentials.base_url, timeout=_TIMEOUT) as http:
        client = _client(credentials, http)
        created = await client.create_run(request)
        run_id = str(created["runId"])
        suite = created.get("suite", {})
        target = created.get("target", {})
        typer.echo(
            f"run created run_id={run_id} "
            f"suite={suite.get('slug')}@{suite.get('version')} "
            f"cases={suite.get('caseCount')} "
            f"target={target.get('name')!r}@{target.get('version')} "
            f"adapter_version={target.get('adapterVersion')}"
        )
        if created.get("reused"):
            typer.echo(f"attempt {attempt} already created this Run; no duplicate was created")
        typer.echo(f"dashboard {created['url']}")
        log.info("run created run_id=%s reused=%s", run_id, bool(created.get("reused")))
        if not execute:
            typer.echo(f"execute it with: redbeak runs create --attempt-id {attempt} --execute ...")
            return 0
        grant = created.get("execution")
        if not isinstance(grant, dict) or not grant.get("value"):
            typer.echo(
                "error: the Run was created but Redbeak issued no execution grant; "
                f"run it with a Project runner key, or retry with --attempt-id {attempt}",
                err=True,
            )
            return 1
        return await _execute_run(
            credentials=credentials,
            client=client,
            run_id=run_id,
            grant=grant,
            adapter_spec=str(adapter_spec),
            runner_id=runner_id,
            checkpoint_dir=checkpoint_dir,
            artifacts_dir=artifacts_dir,
            heartbeat_interval_s=heartbeat_interval_s,
        )


async def _execute_run(
    *,
    credentials: creds.Credentials,
    client: ControlPlaneClient,
    run_id: str,
    grant: dict[str, Any],
    adapter_spec: str,
    runner_id: str | None,
    checkpoint_dir: Path,
    artifacts_dir: Path,
    heartbeat_interval_s: float,
) -> int:
    log = get_logger()
    adapter = load_adapter(adapter_spec)
    key_id = str(grant.get("keyId", ""))
    try:
        async with httpx.AsyncClient(
            base_url=credentials.base_url, timeout=_TIMEOUT
        ) as runner_http:
            result = await _run_with_client(
                adapter=adapter,
                http=runner_http,
                runner_key=str(grant["value"]),
                runner_id=runner_id,
                checkpoint_dir=checkpoint_dir,
                artifacts_dir=artifacts_dir,
                until_idle=True,
                heartbeat_interval_s=heartbeat_interval_s,
            )
    finally:
        # The grant exists only for this command. Release it even on failure.
        if key_id:
            with contextlib.suppress(Exception):
                await client.revoke_grant(key_id)
                log.info("execution grant revoked key_id=%s", key_id)
    typer.echo(f"executed cases={len(result.cases)}")
    for case in result.cases:
        typer.echo(
            f"case case_execution_id={case.case_execution_id} "
            f"status={case.status} turns={len(case.sequences)}"
        )
    # The grant is scoped to this Run, so this can only fail if that scoping did.
    unexpected = [item for item in result.run_ids if item != run_id]
    if unexpected:
        typer.echo(
            "error: the runner executed cases outside the requested Run "
            f"({', '.join(unexpected)}); the requested Run is not proven complete",
            err=True,
        )
        return 1
    if result.stopped:
        typer.echo("stopped before the Run was drained; rerun to continue", err=True)
    status = await client.run_status(run_id)
    _report_status(status)
    return 0
