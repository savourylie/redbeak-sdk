"""Runner-side failures, kept distinct from target failures.

Transport and protocol errors belong to Redbeak. A target that answers poorly
is not an error at all; a target that cannot be reached is classified by the
adapter SDK. This module only names the failures the runner itself can cause
or observe on the wire.
"""

from __future__ import annotations

from typing import Any, ClassVar

from redbeak_adapter_sdk import ExecutionError


class RunnerError(Exception):
    """Root of runner-owned failures."""

    default_code: ClassVar[str] = "runner_failure"
    default_retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.code = code or self.default_code
        self.retryable = self.default_retryable if retryable is None else retryable

    def to_execution_error(self) -> ExecutionError:
        return ExecutionError(
            stage="runner",
            code=self.code,
            message=str(self) or self.code,
            retryable=self.retryable,
        )


class AdapterDiscoveryError(RunnerError):
    """The configured adapter spec could not be imported or constructed."""

    default_code = "adapter_not_found"


class CheckpointError(RunnerError):
    """Local checkpoint state is missing, corrupt, or unwritable."""

    default_code = "checkpoint_invalid"


class ConfigurationError(RunnerError):
    """The runner was started without enough non-secret configuration."""

    default_code = "runner_misconfigured"


class ProtocolError(RunnerError):
    """The server returned a contract-shaped error the runner must honour."""

    default_code = "protocol_violation"

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: dict[str, Any] | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ):
        resolved = code or (body or {}).get("code") or self.default_code
        super().__init__(message, code=str(resolved), retryable=retryable)
        self.status_code = status_code
        self.body = body or {}

    @property
    def error_code(self) -> str:
        return self.code


class TransportError(RunnerError):
    """The runner could not complete an HTTP call."""

    default_code = "runner_transport_failure"
    default_retryable = True


class LeaseLostError(ProtocolError):
    """The current lease is expired or superseded; stop submitting on it."""

    default_code = "lease_expired"

    def __init__(self, message: str, *, status_code: int, body: dict[str, Any] | None = None):
        super().__init__(
            message,
            status_code=status_code,
            body=body,
            code=(body or {}).get("code") or self.default_code,
            retryable=False,
        )


class RunnerCrash(BaseException):
    """Test seam: abort as if the process died after the checkpoint was written.

    Not a ``RunnerError``: it must not be caught by the case-failure path and
    recorded as execution evidence. Tests raise it; production code never does.
    """


__all__ = [
    "AdapterDiscoveryError",
    "CheckpointError",
    "ConfigurationError",
    "LeaseLostError",
    "ProtocolError",
    "RunnerCrash",
    "RunnerError",
    "TransportError",
]
