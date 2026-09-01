"""Bounded retry with error classification.

Retries are for transport and rate limits. A 410 lease loss, a revoked key, or
a sequence conflict is a protocol signal: retrying the same call would hide it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redbeak_runner.errors import ProtocolError, TransportError

T = TypeVar("T")

DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 0.05


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, TransportError):
        return True
    if isinstance(exc, ProtocolError):
        return exc.retryable or exc.status_code == 429
    return False


async def retry_call(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    backoff_s: float = DEFAULT_BACKOFF_S,
) -> T:
    last: BaseException | None = None
    for index in range(max(1, attempts)):
        try:
            return await operation()
        except BaseException as exc:
            last = exc
            if not is_retryable(exc) or index + 1 >= attempts:
                raise
            await asyncio.sleep(backoff_s * (index + 1))
    assert last is not None
    raise last
