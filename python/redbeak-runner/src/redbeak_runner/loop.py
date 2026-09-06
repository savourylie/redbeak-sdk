"""Claim, execute, submit, complete. Resume from the local checkpoint."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from redbeak_adapter_sdk import (
    AdapterError,
    Observation,
    ObservationRequest,
    SessionContext,
    TargetAdapter,
    UserInput,
    normalize_exception,
    observations_to_wire,
)

from redbeak_runner import CONTRACT_VERSION
from redbeak_runner.artifacts import ArtifactStore
from redbeak_runner.checkpoint import (
    Checkpoint,
    clear_checkpoint,
    load_checkpoint,
    save_checkpoint,
)
from redbeak_runner.client import RunnerClient
from redbeak_runner.config import RunnerConfig
from redbeak_runner.errors import LeaseLostError, RunnerCrash
from redbeak_runner.ids import new_uuid
from redbeak_runner.log import get_logger

logger = get_logger()


@dataclass
class CaseResult:
    case_execution_id: str
    status: str
    sequences: tuple[int, ...]


@dataclass
class RunResult:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def case_ids(self) -> list[str]:
        return [item.case_execution_id for item in self.cases]


class LoopHooks:
    """Test seam. Production uses the default no-op instance."""

    async def after_persist(self, checkpoint: Checkpoint) -> None:
        del checkpoint
        return None


async def run_until_idle(
    *,
    config: RunnerConfig,
    adapter: TargetAdapter,
    client: RunnerClient,
    artifacts: ArtifactStore,
    hooks: LoopHooks | None = None,
) -> RunResult:
    """Drain queued work, honouring a leftover checkpoint first."""

    seam = hooks or LoopHooks()
    result = RunResult()
    leftover = load_checkpoint(config.checkpoint_dir)
    if leftover is not None:
        case = await _resume_case(
            config=config,
            adapter=adapter,
            client=client,
            artifacts=artifacts,
            checkpoint=leftover,
            hooks=seam,
        )
        if case is not None:
            result.cases.append(case)

    while True:
        capabilities = await adapter.capabilities()
        claim = {
            "schema_version": CONTRACT_VERSION,
            "runner_id": config.runner_id,
            "runner_version": config.runner_version,
            "contract_version": CONTRACT_VERSION,
            "adapter_version": capabilities.adapter_version,
            "capabilities": capabilities.to_work_claim_capabilities(),
        }
        response = await client.claim(claim)
        if response["status"] == "no_work":
            logger.info(
                "no queued work runner_id=%s retry_after_ms=%s",
                config.runner_id,
                response.get("retry_after_ms"),
            )
            break
        assignment = response["assignment"]
        lease = response["lease"]
        logger.info(
            "claimed case_execution_id=%s run_id=%s scenario_id=%s",
            assignment["case_execution_id"],
            assignment["run_id"],
            assignment["scenario_id"],
        )
        checkpoint = Checkpoint(
            runner_id=config.runner_id,
            case_execution_id=str(assignment["case_execution_id"]),
            lease_id=str(lease["lease_id"]),
            lease_token=str(lease["lease_token"]),
            assignment=dict(assignment),
            submitted_inputs=(),
            pending_input=dict(assignment["input"]),
            pending_observation_request=_optional_mapping(assignment.get("observation_request")),
            pending_turn_idempotency_key=new_uuid(),
            pending_complete_idempotency_key=None,
            phase="turn",
        )
        save_checkpoint(config.checkpoint_dir, checkpoint)
        await seam.after_persist(checkpoint)
        case = await _run_case(
            config=config,
            adapter=adapter,
            client=client,
            artifacts=artifacts,
            checkpoint=checkpoint,
            hooks=seam,
            replay_submitted=False,
        )
        if case is not None:
            result.cases.append(case)
        if not config.until_idle:
            break
    return result


async def _resume_case(
    *,
    config: RunnerConfig,
    adapter: TargetAdapter,
    client: RunnerClient,
    artifacts: ArtifactStore,
    checkpoint: Checkpoint,
    hooks: LoopHooks,
) -> CaseResult | None:
    logger.info(
        "resuming case_execution_id=%s phase=%s submitted=%s",
        checkpoint.case_execution_id,
        checkpoint.phase,
        len(checkpoint.submitted_inputs),
    )
    if checkpoint.phase != "complete":
        try:
            await client.heartbeat(
                checkpoint.case_execution_id,
                {
                    "schema_version": CONTRACT_VERSION,
                    "lease_token": checkpoint.lease_token,
                    "progress": {"sequence": len(checkpoint.submitted_inputs)},
                },
            )
        except LeaseLostError:
            logger.info(
                "resume lease lost case_execution_id=%s; claiming again",
                checkpoint.case_execution_id,
            )
            clear_checkpoint(config.checkpoint_dir)
            return None
    return await _run_case(
        config=config,
        adapter=adapter,
        client=client,
        artifacts=artifacts,
        checkpoint=checkpoint,
        hooks=hooks,
        replay_submitted=True,
    )


async def _run_case(
    *,
    config: RunnerConfig,
    adapter: TargetAdapter,
    client: RunnerClient,
    artifacts: ArtifactStore,
    checkpoint: Checkpoint,
    hooks: LoopHooks,
    replay_submitted: bool,
) -> CaseResult | None:
    assignment = checkpoint.assignment
    context = SessionContext.from_assignment(assignment)
    sequences: list[int] = []
    parent_ids: list[str] = []
    capabilities = await adapter.capabilities()
    try:
        if checkpoint.phase == "complete":
            accepted = await _complete(
                config=config,
                adapter=adapter,
                client=client,
                artifacts=artifacts,
                checkpoint=checkpoint,
                context=context,
                capabilities_version=capabilities.adapter_version,
                parent_ids=tuple(parent_ids),
                extra_observations=[],
                hooks=hooks,
            )
            clear_checkpoint(config.checkpoint_dir)
            return CaseResult(
                case_execution_id=checkpoint.case_execution_id,
                status=str(accepted["status"]),
                sequences=tuple(range(len(checkpoint.submitted_inputs))),
            )

        await adapter.reset(context)
        if replay_submitted:
            for item in checkpoint.submitted_inputs:
                await adapter.send(UserInput.from_dict(item), context)

        current = checkpoint
        while True:
            pending = current.pending_input
            if pending is None:
                break
            started = time.perf_counter()
            user_input = UserInput.from_dict(pending)
            output = await _with_heartbeat(
                client,
                current,
                config.heartbeat_interval_s,
                adapter.send(user_input, context),
            )
            observations: list[Observation] = []
            if current.pending_observation_request is not None:
                request = ObservationRequest.from_dict(current.pending_observation_request)
                observations = await adapter.observe(request, context)
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            idempotency_key = current.pending_turn_idempotency_key or new_uuid()
            if current.pending_turn_idempotency_key is None:
                current = _replace(
                    current,
                    pending_turn_idempotency_key=idempotency_key,
                )
                save_checkpoint(config.checkpoint_dir, current)
                await hooks.after_persist(current)
            # Replaying inputs restores adapter state, but the already prepared
            # output/observations/timing must not change under an idempotency key.
            submission = current.pending_submission or {
                "schema_version": CONTRACT_VERSION,
                "lease_token": current.lease_token,
                "sequence": user_input.sequence,
                "idempotency_key": idempotency_key,
                "output": output.to_dict(),
                "observations": observations_to_wire(observations),
                "operational": {
                    "duration_ms": duration_ms,
                    "retry_count": 0,
                    "runner_version": config.runner_version,
                    "adapter_version": capabilities.adapter_version,
                },
            }
            if current.pending_submission is None:
                current = _replace(current, pending_submission=submission)
                save_checkpoint(config.checkpoint_dir, current)
                await hooks.after_persist(current)
            next_action = await client.submit_turn(current.case_execution_id, submission)
            turn_manifest = artifacts.write_turn(
                run_id=str(assignment["run_id"]),
                case_execution_id=current.case_execution_id,
                sequence=user_input.sequence,
                submission=submission,
                next_action=next_action,
            )
            parent_ids.append(str(turn_manifest["artifact_id"]))
            if submission["observations"]:
                obs_manifest = artifacts.write_observations(
                    run_id=str(assignment["run_id"]),
                    case_execution_id=current.case_execution_id,
                    sequence=user_input.sequence,
                    observations=list(submission["observations"]),
                    parent_artifact_id=str(turn_manifest["artifact_id"]),
                )
                parent_ids.append(str(obs_manifest["artifact_id"]))
            sequences.append(user_input.sequence)
            submitted = (*current.submitted_inputs, dict(pending))
            action = str(next_action["action"])
            if action == "continue":
                current = _replace(
                    current,
                    submitted_inputs=submitted,
                    pending_input=dict(next_action["input"]),
                    pending_observation_request=_optional_mapping(
                        next_action.get("observation_request")
                    ),
                    pending_turn_idempotency_key=new_uuid(),
                    pending_submission=None,
                    phase="turn",
                )
                save_checkpoint(config.checkpoint_dir, current)
                await hooks.after_persist(current)
                continue
            extra: list[dict[str, Any]] = []
            if action == "finish" and next_action.get("observation_request"):
                request = ObservationRequest.from_dict(next_action["observation_request"])
                extra = observations_to_wire(await adapter.observe(request, context))
            cancel_reason = str(next_action["reason"]) if action == "cancel" else None
            complete_key = current.pending_complete_idempotency_key or new_uuid()
            current = _replace(
                current,
                submitted_inputs=submitted,
                pending_input=None,
                pending_observation_request=None,
                pending_turn_idempotency_key=None,
                pending_complete_idempotency_key=complete_key,
                pending_submission=None,
                phase="complete",
            )
            status = "canceled" if action == "cancel" else "completed"
            accepted = await _complete(
                config=config,
                adapter=adapter,
                client=client,
                artifacts=artifacts,
                checkpoint=current,
                context=context,
                capabilities_version=capabilities.adapter_version,
                parent_ids=tuple(parent_ids),
                extra_observations=extra,
                status=status,
                cancel_reason=cancel_reason,
                hooks=hooks,
            )
            await adapter.close(context)
            clear_checkpoint(config.checkpoint_dir)
            return CaseResult(
                case_execution_id=current.case_execution_id,
                status=str(accepted["status"]),
                sequences=tuple(sequences),
            )
        await adapter.close(context)
        clear_checkpoint(config.checkpoint_dir)
        return None
    except RunnerCrash:
        raise
    except LeaseLostError:
        logger.info("lease lost case_execution_id=%s", checkpoint.case_execution_id)
        await _close_quietly(adapter, context)
        clear_checkpoint(config.checkpoint_dir)
        return None
    except (AdapterError, Exception) as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        error = normalize_exception(exc)
        logger.info(
            "case failed case_execution_id=%s stage=%s code=%s",
            checkpoint.case_execution_id,
            error.stage,
            error.code,
        )
        complete_key = checkpoint.pending_complete_idempotency_key or new_uuid()
        failed = _replace(
            checkpoint,
            pending_complete_idempotency_key=complete_key,
            pending_submission=None,
            phase="complete",
        )
        with suppress(LeaseLostError):
            await _complete(
                config=config,
                adapter=adapter,
                client=client,
                artifacts=artifacts,
                checkpoint=failed,
                context=context,
                capabilities_version=capabilities.adapter_version,
                parent_ids=tuple(parent_ids),
                extra_observations=[],
                status="execution_failed",
                error=error.to_dict(),
                hooks=hooks,
            )
        await _close_quietly(adapter, context)
        clear_checkpoint(config.checkpoint_dir)
        return CaseResult(
            case_execution_id=checkpoint.case_execution_id,
            status="recorded",
            sequences=tuple(sequences),
        )


async def _complete(
    *,
    config: RunnerConfig,
    adapter: TargetAdapter,
    client: RunnerClient,
    artifacts: ArtifactStore,
    checkpoint: Checkpoint,
    context: SessionContext,
    capabilities_version: str,
    parent_ids: tuple[str, ...],
    extra_observations: list[dict[str, Any]],
    hooks: LoopHooks,
    status: str = "completed",
    cancel_reason: str | None = None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    del adapter, context
    body: dict[str, Any] = {
        "schema_version": CONTRACT_VERSION,
        "lease_token": checkpoint.lease_token,
        "idempotency_key": checkpoint.pending_complete_idempotency_key or new_uuid(),
        "status": status,
        "observations": extra_observations,
        "operational": {
            "duration_ms": 0,
            "retry_count": 0,
            "runner_version": config.runner_version,
            "adapter_version": capabilities_version,
        },
    }
    if cancel_reason is not None:
        body["cancel_reason"] = cancel_reason
    if error is not None:
        body["error"] = error
    if checkpoint.pending_submission is not None:
        body = checkpoint.pending_submission
    else:
        prepared = _replace(checkpoint, pending_submission=body)
        save_checkpoint(
            config.checkpoint_dir,
            prepared,
        )
        await hooks.after_persist(prepared)
    accepted = await client.complete(checkpoint.case_execution_id, body)
    artifacts.write_completion(
        run_id=str(checkpoint.assignment["run_id"]),
        case_execution_id=checkpoint.case_execution_id,
        completion=body,
        accepted=accepted,
        parent_ids=parent_ids,
    )
    return accepted


async def _with_heartbeat(
    client: RunnerClient,
    checkpoint: Checkpoint,
    interval_s: float,
    awaitable: Awaitable[Any],
) -> Any:
    if interval_s <= 0:
        return await awaitable
    stop = asyncio.Event()

    async def beat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                try:
                    await client.heartbeat(
                        checkpoint.case_execution_id,
                        {
                            "schema_version": CONTRACT_VERSION,
                            "lease_token": checkpoint.lease_token,
                        },
                    )
                except LeaseLostError:
                    stop.set()
                    return

    task = asyncio.create_task(beat())
    try:
        return await awaitable
    finally:
        stop.set()
        await task


def _replace(checkpoint: Checkpoint, **changes: Any) -> Checkpoint:
    data = checkpoint.to_dict()
    data.update(changes)
    if "submitted_inputs" in changes and isinstance(changes["submitted_inputs"], tuple):
        data["submitted_inputs"] = list(changes["submitted_inputs"])
    return Checkpoint.from_dict(data)


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return dict(value)


async def _close_quietly(adapter: TargetAdapter, context: SessionContext) -> None:
    with suppress(AdapterError):
        await adapter.close(context)
