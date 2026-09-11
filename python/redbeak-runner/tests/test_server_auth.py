"""Project-scoped keys, revocation, and cross-project isolation."""

from __future__ import annotations

from _runner_fixtures import CASE_A, PROJECT_ID, two_turn_case
from redbeak_reference_server import ReferenceServer
from redbeak_runner import CONTRACT_VERSION, RUNNER_VERSION
from redbeak_runner.ids import new_uuid

OTHER_PROJECT = "77777777-7777-4777-8777-777777777777"


def _claim_body(runner_id: str | None = None) -> dict[str, str | dict[str, int]]:
    return {
        "schema_version": CONTRACT_VERSION,
        "runner_id": runner_id or new_uuid(),
        "runner_version": RUNNER_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": "0.1.0",
        "capabilities": {"max_concurrent_cases": 1},
    }


def test_missing_or_revoked_key_is_unauthorized() -> None:
    server = ReferenceServer()
    server.enqueue(two_turn_case())
    status, body = server.claim(None, _claim_body())
    assert status == 401
    assert body["code"] == "unauthorized"

    raw = server.create_key(PROJECT_ID)
    server.revoke_key(raw)
    status, body = server.claim(raw, _claim_body())
    assert status == 401
    assert body["code"] == "unauthorized"


def test_runner_cannot_cross_project_boundaries() -> None:
    server = ReferenceServer()
    server.enqueue(two_turn_case())
    foreign = server.create_key(OTHER_PROJECT)
    status, body = server.claim(foreign, _claim_body())
    assert status == 200
    assert body["status"] == "no_work"

    home = server.create_key(PROJECT_ID)
    status, leased = server.claim(home, _claim_body())
    assert status == 200
    assert leased["status"] == "leased"
    token = leased["lease"]["lease_token"]

    turn = {
        "schema_version": CONTRACT_VERSION,
        "lease_token": token,
        "sequence": 0,
        "idempotency_key": new_uuid(),
        "output": {"type": "agent_message", "content": "x"},
        "observations": [],
        "operational": {"duration_ms": 1},
    }
    status, body = server.submit_turn(foreign, CASE_A, turn)
    assert status == 403
    assert body["code"] == "forbidden"
