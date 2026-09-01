"""ASGI surface for the in-memory reference server."""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from redbeak_runner.server.runtime import ReferenceServer


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


async def _json_body(request: Request) -> Any:
    try:
        return await request.json()
    except json.JSONDecodeError:
        return None


def create_app(server: ReferenceServer) -> Starlette:
    async def claim(request: Request) -> Response:
        body = await _json_body(request)
        status, payload = server.claim(_bearer(request), body)
        return JSONResponse(payload, status_code=status)

    async def turn(request: Request) -> Response:
        body = await _json_body(request)
        status, payload = server.submit_turn(
            _bearer(request), request.path_params["case_execution_id"], body
        )
        return JSONResponse(payload, status_code=status)

    async def complete(request: Request) -> Response:
        body = await _json_body(request)
        status, payload = server.complete(
            _bearer(request), request.path_params["case_execution_id"], body
        )
        return JSONResponse(payload, status_code=status)

    async def heartbeat(request: Request) -> Response:
        body = await _json_body(request)
        status, payload = server.heartbeat(
            _bearer(request), request.path_params["case_execution_id"], body
        )
        return JSONResponse(payload, status_code=status)

    routes = [
        Route("/v1/runner/work/claim", claim, methods=["POST"]),
        Route("/v1/case-executions/{case_execution_id}/turn", turn, methods=["POST"]),
        Route("/v1/case-executions/{case_execution_id}/complete", complete, methods=["POST"]),
        Route("/v1/case-executions/{case_execution_id}/heartbeat", heartbeat, methods=["POST"]),
    ]
    return Starlette(routes=routes)
