"""Duplicate turn and complete submissions create one authoritative event."""

from __future__ import annotations

from _runner_fixtures import CASE_A, PROJECT_ID, two_turn_case
from redbeak_reference_server import QueuedCase, ReferenceServer
from redbeak_runner import CONTRACT_VERSION, RUNNER_VERSION
from redbeak_runner.ids import new_uuid


def test_duplicate_turn_submissions_create_one_event() -> None:
    server = ReferenceServer()
    server.enqueue(two_turn_case())
    key = server.create_key(PROJECT_ID)
    _, leased = server.claim(
        key,
        {
            "schema_version": CONTRACT_VERSION,
            "runner_id": new_uuid(),
            "runner_version": RUNNER_VERSION,
            "contract_version": CONTRACT_VERSION,
            "adapter_version": "0.1.0",
            "capabilities": {"max_concurrent_cases": 1},
        },
    )
    token = leased["lease"]["lease_token"]
    idempotency_key = new_uuid()
    body = {
        "schema_version": CONTRACT_VERSION,
        "lease_token": token,
        "sequence": 0,
        "idempotency_key": idempotency_key,
        "output": {"type": "agent_message", "content": "first"},
        "observations": [],
        "operational": {"duration_ms": 1},
    }
    status, first = server.submit_turn(key, CASE_A, body)
    assert status == 200
    assert first["action"] == "continue"
    status, replayed = server.submit_turn(key, CASE_A, body)
    assert status == 200
    assert replayed == first

    other_key = new_uuid()
    duplicate_seq = dict(body)
    duplicate_seq["idempotency_key"] = other_key
    duplicate_seq["output"] = {"type": "agent_message", "content": "should-not-replace"}
    status, authoritative = server.submit_turn(key, CASE_A, duplicate_seq)
    assert status == 200
    assert authoritative == first

    events = server.events_for(CASE_A)
    assert len(events) == 1
    assert events[0]["output"]["content"] == "first"
    assert "lease_token" not in events[0]


def test_duplicate_completion_does_not_queue_twice() -> None:
    server = ReferenceServer()
    server.enqueue(
        QueuedCase(
            project_id=PROJECT_ID,
            run_id="22222222-2222-4222-8222-222222222222",
            case_execution_id=CASE_A,
            scenario_id="44444444-4444-4444-8444-444444444444",
            scenario_external_id="one",
            setup={"type": "inline", "payload": {}},
            turns=({"sequence": 0, "type": "user_message", "content": "only"},),
        )
    )
    key = server.create_key(PROJECT_ID)
    _, leased = server.claim(
        key,
        {
            "schema_version": CONTRACT_VERSION,
            "runner_id": new_uuid(),
            "runner_version": RUNNER_VERSION,
            "contract_version": CONTRACT_VERSION,
            "adapter_version": "0.1.0",
            "capabilities": {"max_concurrent_cases": 1},
        },
    )
    token = leased["lease"]["lease_token"]
    server.submit_turn(
        key,
        CASE_A,
        {
            "schema_version": CONTRACT_VERSION,
            "lease_token": token,
            "sequence": 0,
            "idempotency_key": new_uuid(),
            "output": {"type": "agent_message", "content": "ok"},
            "observations": [],
            "operational": {"duration_ms": 1},
        },
    )
    complete_key = new_uuid()
    completion = {
        "schema_version": CONTRACT_VERSION,
        "lease_token": token,
        "idempotency_key": complete_key,
        "status": "completed",
        "observations": [],
        "operational": {"duration_ms": 1},
    }
    status, accepted = server.complete(key, CASE_A, completion)
    assert status == 202
    assert accepted["status"] == "evaluation_queued"
    status, replayed = server.complete(key, CASE_A, completion)
    assert status == 202
    assert replayed == accepted
    events = [item for item in server.events_for(CASE_A) if item.get("status") == "completed"]
    assert len(events) == 1
