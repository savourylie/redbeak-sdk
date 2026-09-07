"""HTTP client for the Redbeak CLI control-plane surface (`cli 0.1`).

This is not the runner protocol. The frozen contract `0.1` in
``contracts/openapi/runner-0.1.yaml`` covers claim, turn, complete and heartbeat
and is untouched by this client. The surface here creates and reads Runs, is
authenticated by a CLI token, and never carries scenario content, ground truth,
expected outcomes, rubrics or scores.
"""

from __future__ import annotations

from typing import Any

import httpx

from redbeak_runner.errors import ProtocolError, TransportError

CLI_API_VERSION = "0.1"


class ControlPlaneError(ProtocolError):
    """The control plane rejected a CLI request."""

    default_code = "cli_request_rejected"


class ControlPlaneClient:
    """Thin, explicit client. Every method names one endpoint."""

    def __init__(self, http: httpx.AsyncClient, *, token: str, project_id: str) -> None:
        self._http = http
        self._token = token
        self._project = project_id

    @property
    def project_id(self) -> str:
        return self._project

    def _headers(self, *, body: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"}
        if body:
            headers["Content-Type"] = "application/json"
        return headers

    def _base(self) -> str:
        return f"/cli/v1/projects/{self._project}"

    async def versions(self) -> dict[str, Any]:
        return await self._request("GET", f"{self._base()}/versions", None, (200,))

    async def create_run(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"{self._base()}/runs", body, (200, 201))

    async def run_status(self, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"{self._base()}/runs/{run_id}", None, (200,))

    async def revoke_grant(self, key_id: str) -> dict[str, Any]:
        return await self._request(
            "POST", f"{self._base()}/execution-grants/{key_id}/revoke", {}, (200,)
        )

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        try:
            response = await self._http.request(
                method, path, json=body, headers=self._headers(body=body is not None)
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"control-plane call failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if response.status_code in expected:
            return payload
        raise ControlPlaneError(
            _message(response.status_code, str(payload.get("code", ""))),
            status_code=response.status_code,
            body=payload,
            code=str(payload.get("code") or ControlPlaneError.default_code),
            retryable=response.status_code in (429, 502, 503, 504),
        )


#: Actionable text per failure. The server never returns a message body, so the
#: mapping lives here where it can be reviewed alongside the commands.
_MESSAGES = {
    "unauthorized": (
        "The CLI token was rejected. It may be revoked, for another Project, "
        "or a runner key: run 'redbeak auth login' with a token from Settings."
    ),
    "not_found": (
        "The Project or Run is not visible to this token. Check the Project ID, "
        "and that the token was issued for that Project."
    ),
    "suite_version_not_found": (
        "No SuiteVersion matched. Run 'redbeak runs versions' to list what this Project has."
    ),
    "suite_version_ambiguous": (
        "Several SuiteVersions matched. Pass --suite-version, or --suite-version-id."
    ),
    "target_version_not_found": (
        "No TargetVersion matched. Run 'redbeak runs versions', and register the target first."
    ),
    "target_version_ambiguous": (
        "Several TargetVersions matched. Pass --target-version, or --target-version-id."
    ),
    "attempt_conflict": (
        "This attempt ID already created a Run for a different suite or target. "
        "Retry without --attempt-id to start a new attempt."
    ),
    "invalid_request": "The request was rejected as malformed.",
    "service_unavailable": "Redbeak is unavailable. The Run was not created.",
}


def _message(status: int, code: str) -> str:
    return _MESSAGES.get(code) or f"Redbeak rejected the request (HTTP {status})."
