"""First checkpoint: four domains, golden target, restart without duplicate evidence."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

import httpx
from _runner_fixtures import CREATED_AT, PROJECT_ID, RUN_ID, run
from redbeak_bfcl_demo.driver import fact_names_for, load_scenarios, load_suite
from redbeak_bfcl_demo.golden import create_adapter
from redbeak_bfcl_demo.importer import default_output_dir
from redbeak_bfcl_demo.importer import stable_uuid as demo_uuid
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.checkpoint import Checkpoint
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig
from redbeak_runner.errors import RunnerCrash
from redbeak_runner.ids import new_uuid
from redbeak_runner.loop import LoopHooks, run_until_idle
from redbeak_runner.server import QueuedCase, ReferenceServer, create_app

WALKING = "bfcl_walking_skeleton"
DOMAINS = {
    "gorilla_file_system",
    "trading_bots",
    "travel_booking",
    "vehicle_control",
}


class CrashAfterFirstCompletion(LoopHooks):
    def __init__(self) -> None:
        self.seen = False

    async def after_persist(self, checkpoint: Checkpoint) -> None:
        if checkpoint.phase == "complete" and not self.seen:
            self.seen = True
            raise RunnerCrash("killed after first case reached complete")


def walking_cases() -> list[QueuedCase]:
    dataset = default_output_dir()
    suite = load_suite(dataset, WALKING)
    scenarios = load_scenarios(dataset)
    cases: list[QueuedCase] = []
    for entry in suite["cases"]:
        external_id = str(entry["external_id"])
        scenario = scenarios[external_id]
        fact_names = fact_names_for(scenario["involved_classes"])
        cases.append(
            QueuedCase(
                project_id=PROJECT_ID,
                run_id=RUN_ID,
                case_execution_id=demo_uuid("runner-case", RUN_ID, external_id),
                scenario_id=demo_uuid("runner-scenario", external_id),
                scenario_external_id=external_id,
                setup=dict(scenario["setup"]),
                turns=tuple(dict(turn) for turn in scenario["turns"]),
                observation_request={"fact_names": list(fact_names)},
                target_version="test-only-0.1.0",
            )
        )
    return cases


async def _run_skeleton(
    tmp_path: Path,
    server: ReferenceServer,
    key: str,
    *,
    hooks: LoopHooks | None = None,
    runner_id: str,
) -> None:
    adapter = create_adapter()
    app = create_app(server)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        client = RunnerClient(http, runner_key=key)
        config = RunnerConfig(
            runner_id=runner_id,
            adapter_spec="redbeak_bfcl_demo.golden",
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


def test_four_domain_checkpoint_survives_restart(tmp_path: Path) -> None:
    cases = walking_cases()
    assert {case.scenario_external_id for case in cases}
    domains = set()
    dataset = default_output_dir()
    suite = load_suite(dataset, WALKING)
    for entry in suite["cases"]:
        domains.add(str(entry["domain"]))
    assert domains == DOMAINS
    assert len(cases) == 4

    server = ReferenceServer()
    for case in cases:
        server.enqueue(case)
    key = server.create_key(PROJECT_ID)
    runner_id = new_uuid()

    with suppress(RunnerCrash):
        run(
            _run_skeleton(
                tmp_path,
                server,
                key,
                hooks=CrashAfterFirstCompletion(),
                runner_id=runner_id,
            )
        )

    run(_run_skeleton(tmp_path, server, key, runner_id=runner_id))

    for case in cases:
        record = server.case(case.case_execution_id)
        assert record.completion is not None
        sequences = [turn.sequence for turn in record.turns]
        assert sequences == list(range(len(sequences)))
        assert len(sequences) == len(set(sequences))
        completion = (
            tmp_path / "artifacts" / RUN_ID / "cases" / case.case_execution_id / "completion.json"
        )
        assert completion.is_file()
        assert (
            tmp_path
            / "artifacts"
            / RUN_ID
            / "cases"
            / case.case_execution_id
            / "turn-000.manifest.json"
        ).is_file()
