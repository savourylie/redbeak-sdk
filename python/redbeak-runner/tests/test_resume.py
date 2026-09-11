"""Killing the runner mid-case resumes at the server-authoritative sequence."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import httpx
from _runner_fixtures import (
    CASE_A,
    CREATED_AT,
    PROJECT_ID,
    NoteAdapter,
    run,
    two_turn_case,
)
from redbeak_reference_server import ReferenceServer, create_app
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.checkpoint import Checkpoint, load_checkpoint
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig
from redbeak_runner.errors import RunnerCrash
from redbeak_runner.ids import new_uuid
from redbeak_runner.loop import LoopHooks, run_until_idle


class CrashAfterFirstTurn(LoopHooks):
    def __init__(self) -> None:
        self.turns = 0

    async def after_persist(self, checkpoint: Checkpoint) -> None:
        phase = checkpoint.phase
        submitted = checkpoint.submitted_inputs
        if phase == "turn" and submitted:
            self.turns += 1
            if self.turns == 1:
                raise RunnerCrash("killed after first accepted turn")


async def _drive(
    tmp_path: Path,
    server: ReferenceServer,
    key: str,
    adapter: NoteAdapter,
    *,
    hooks: LoopHooks | None = None,
    runner_id: str,
) -> None:
    app = create_app(server)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = RunnerClient(http, runner_key=key)
        config = RunnerConfig(
            runner_id=runner_id,
            adapter_spec="note",
            runner_key=key,
            base_url="http://test",
            checkpoint_dir=tmp_path / "checkpoint",
            artifacts_dir=tmp_path / "artifacts",
            until_idle=True,
            heartbeat_interval_s=0,
        )
        store = ArtifactStore(config.artifacts_dir, created_at=CREATED_AT)
        await run_until_idle(
            config=config, adapter=adapter, client=client, artifacts=store, hooks=hooks
        )


def test_restart_resumes_without_duplicate_events(tmp_path: Path) -> None:
    server = ReferenceServer()
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    runner_id = new_uuid()
    adapter = NoteAdapter()
    hooks = CrashAfterFirstTurn()

    with suppress(RunnerCrash):
        run(_drive(tmp_path, server, key, adapter, hooks=hooks, runner_id=runner_id))

    checkpoint = load_checkpoint(tmp_path / "checkpoint")
    assert checkpoint is not None
    assert len(checkpoint.submitted_inputs) == 1
    assert server.events_for(CASE_A)  # first turn is on the server

    resumed = NoteAdapter()
    run(_drive(tmp_path, server, key, resumed, runner_id=runner_id))
    events = server.events_for(CASE_A)
    turn_events = [item for item in events if "sequence" in item]
    assert [item["sequence"] for item in turn_events] == [0, 1]
    assert server.case(CASE_A).completion is not None
    assert load_checkpoint(tmp_path / "checkpoint") is None
    # The resumed adapter replayed the first user turn to restore session
    # state, then executed the pending second turn.
    assert resumed.sent[-1] == "again"
