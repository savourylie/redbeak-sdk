"""HTTP client for runner protocol v0."""

from __future__ import annotations

from typing import Any

import httpx
import redbeak_contracts as rc

from redbeak_runner.errors import LeaseLostError, ProtocolError, TransportError
from redbeak_runner.retry import retry_call


class RunnerClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        runner_key: str,
        max_retries: int = 3,
    ) -> None:
        self._http = http
        self._runner_key = runner_key
        self._max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._runner_key}",
            "Content-Type": "application/json",
        }

    async def claim(self, body: dict[str, Any]) -> dict[str, Any]:
        rc.validate("work-claim-request", body)
        payload = await self._post("/v1/runner/work/claim", body, expected=(200,))
        rc.validate("work-claim-response", payload)
        return payload

    async def submit_turn(self, case_execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        rc.validate("turn-submission", body)
        payload = await self._post(
            f"/v1/case-executions/{case_execution_id}/turn", body, expected=(200,)
        )
        rc.validate("next-action", payload)
        return payload

    async def complete(self, case_execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        rc.validate("case-completion", body)
        payload = await self._post(
            f"/v1/case-executions/{case_execution_id}/complete", body, expected=(202,)
        )
        rc.validate("completion-accepted", payload)
        return payload

    async def heartbeat(self, case_execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        rc.validate("heartbeat-request", body)
        payload = await self._post(
            f"/v1/case-executions/{case_execution_id}/heartbeat", body, expected=(200,)
        )
        rc.validate("heartbeat-response", payload)
        return payload

    async def _post(
        self, path: str, body: dict[str, Any], *, expected: tuple[int, ...]
    ) -> dict[str, Any]:
        async def once() -> dict[str, Any]:
            try:
                response = await self._http.post(path, json=body, headers=self._headers())
            except httpx.HTTPError as exc:
                raise TransportError(f"runner HTTP call failed: {exc}") from exc
            payload = _json(response)
            if response.status_code in expected:
                return payload
            raise _protocol_error(response.status_code, payload)

        return await retry_call(once, attempts=self._max_retries)


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        payload = {
            "schema_version": rc.CONTRACT_VERSION,
            "code": "internal_error",
            "message": "non-json",
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": rc.CONTRACT_VERSION,
            "code": "internal_error",
            "message": "non-object error body",
        }
    return payload


def _protocol_error(status_code: int, payload: dict[str, Any]) -> ProtocolError:
    code = str(payload.get("code", "internal_error"))
    message = str(payload.get("message", "runner protocol error"))
    retryable = status_code == 429 or code == "rate_limited"
    if status_code == 410 or code in {"lease_expired", "lease_invalid"}:
        return LeaseLostError(message, status_code=status_code, body=payload)
    return ProtocolError(
        message, status_code=status_code, body=payload, code=code, retryable=retryable
    )
