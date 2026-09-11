"""Lease expiry, stale tokens, and reclaim by another runner."""

from __future__ import annotations

from datetime import timedelta

from _runner_fixtures import CASE_A, PROJECT_ID, frozen_now, two_turn_case
from redbeak_reference_server import FrozenClock, ReferenceServer
from redbeak_runner import CONTRACT_VERSION, RUNNER_VERSION
from redbeak_runner.ids import new_uuid


def _claim(runner_id: str) -> dict[str, object]:
    return {
        "schema_version": CONTRACT_VERSION,
        "runner_id": runner_id,
        "runner_version": RUNNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": "0.1.0",
        "capabilities": {"max_concurrent_cases": 1},
    }


def _turn(token: str, sequence: int, key: str) -> dict[str, object]:
    return {
        "schema_version": CONTRACT_VERSION,
        "lease_token": token,
        "sequence": sequence,
        "idempotency_key": key,
        "output": {"type": "agent_message", "content": "x"},
        "observations": [],
        "operational": {"duration_ms": 1},
    }


def _complete(token: str, key: str) -> dict[str, object]:
    return {
        "schema_version": CONTRACT_VERSION,
        "lease_token": token,
        "idempotency_key": key,
        "status": "completed",
        "observations": [],
        "operational": {"duration_ms": 1},
    }


def test_expired_work_is_requeued_and_claimable_by_another_runner() -> None:
    clock = FrozenClock(frozen_now())
    server = ReferenceServer(clock=clock, lease_ttl=timedelta(seconds=10))
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    first = new_uuid()
    second = new_uuid()

    status, leased = server.claim(key, _claim(first))
    assert status == 200
    token = leased["lease"]["lease_token"]
    status, action = server.submit_turn(key, CASE_A, _turn(token, 0, new_uuid()))
    assert status == 200
    assert action["action"] == "continue"

    clock.advance(11)
    status, body = server.submit_turn(key, CASE_A, _turn(token, 1, new_uuid()))
    assert status == 410
    assert body["code"] == "lease_expired"

    status, again = server.claim(key, _claim(second))
    assert status == 200
    assert again["status"] == "leased"
    assert again["assignment"]["case_execution_id"] == CASE_A
    new_token = again["lease"]["lease_token"]
    assert new_token != token

    status, body = server.complete(key, CASE_A, _complete(token, new_uuid()))
    assert status == 409
    assert body["code"] == "lease_invalid"


def test_stale_lease_cannot_complete_after_a_new_claim() -> None:
    clock = FrozenClock(frozen_now())
    server = ReferenceServer(clock=clock, lease_ttl=timedelta(seconds=10))
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    first = new_uuid()
    other = new_uuid()

    _, leased = server.claim(key, _claim(first))
    stale = leased["lease"]["lease_token"]
    clock.advance(11)
    _, claimed = server.claim(key, _claim(other))
    assert claimed["status"] == "leased"

    status, body = server.complete(key, CASE_A, _complete(stale, new_uuid()))
    assert status == 409
    assert body["code"] == "lease_invalid"


def test_heartbeat_cannot_resurrect_an_expired_lease() -> None:
    clock = FrozenClock(frozen_now())
    server = ReferenceServer(clock=clock, lease_ttl=timedelta(seconds=5))
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    _, leased = server.claim(key, _claim(new_uuid()))
    token = leased["lease"]["lease_token"]
    clock.advance(6)
    status, body = server.heartbeat(
        key,
        CASE_A,
        {"schema_version": CONTRACT_VERSION, "lease_token": token},
    )
    assert status == 410
    assert body["code"] == "lease_expired"
