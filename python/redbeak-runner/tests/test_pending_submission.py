"""Lost responses must replay the original request, including timing and errors."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
from _runner_fixtures import CASE_A, CREATED_AT, PROJECT_ID, NoteAdapter, run, two_turn_case
from redbeak_adapter_sdk import AgentOutput, SessionContext, UserInput
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.checkpoint import load_checkpoint
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig
from redbeak_runner.errors import RunnerCrash
from redbeak_runner.ids import new_uuid
from redbeak_runner.loop import run_until_idle
from redbeak_runner.server import ReferenceServer, create_app


class ChangingNote(NoteAdapter):
    def __init__(self, reply: str, *, fails: bool = False) -> None:
        super().__init__()
        self.reply = reply
        self.fails = fails
        self.resets = 0

    async def reset(self, context: SessionContext) -> None:
        self.resets += 1
        await super().reset(context)

    async def send(self, input: UserInput, context: SessionContext) -> AgentOutput:
        if self.fails:
            raise TimeoutError("synthetic adapter timeout")
        await super().send(input, context)
        return AgentOutput(content=self.reply)


@pytest.mark.parametrize(
    ("operation", "fails"), [("turn", False), ("complete", False), ("complete", True)]
)
def test_lost_response_replays_original_request(
    tmp_path: Path, operation: str, fails: bool
) -> None:
    server = ReferenceServer()
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    runner_id = new_uuid()
    requests: list[dict[str, Any]] = []

    class Client(RunnerClient):
        async def _post(
            self, path: str, body: dict[str, Any], *, expected: tuple[int, ...]
        ) -> dict[str, Any]:
            observed = path.endswith("/" + operation)
            if observed:
                requests.append(deepcopy(body))
            response = await super()._post(path, body, expected=expected)
            if observed and len(requests) == 1:
                raise RunnerCrash("server accepted, response was lost")
            return response

    async def drive(adapter: ChangingNote) -> None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(server)), base_url="http://test"
        ) as http:
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
            await run_until_idle(
                config=config,
                adapter=adapter,
                client=Client(http, runner_key=key),
                artifacts=ArtifactStore(config.artifacts_dir, created_at=CREATED_AT),
            )

    with pytest.raises(RunnerCrash):
        run(drive(ChangingNote("original output", fails=fails)))
    checkpoint = load_checkpoint(tmp_path / "checkpoint")
    assert checkpoint is not None
    assert checkpoint.pending_submission == requests[0]
    path = tmp_path / "checkpoint/runner-checkpoint.json"
    assert path.stat().st_mode & 0o777 == 0o600
    assert key not in path.read_text()
    original_events = deepcopy(server.events_for(CASE_A))

    resumed = ChangingNote("different output after restart")
    run(drive(resumed))
    assert requests[1] == requests[0]
    assert server.events_for(CASE_A)[: len(original_events)] == original_events
    assert load_checkpoint(tmp_path / "checkpoint") is None
    completion = server.case(CASE_A).completion
    assert completion is not None
    assert completion.event["status"] == ("execution_failed" if fails else "completed")
    if operation == "complete":
        assert resumed.resets == 0
        assert resumed.sent == []
