"""Adapter failures, and how they are told apart from target failures.

The distinction this module exists to protect is the one the contract states in
``execution_error``: a target that answers *poorly* is not an error at all, a
target that is unreachable or crashed is ``target_unavailable`` and belongs to
the system under test, and everything else is a Redbeak-side or integration
fault that must be reported separately from target results.

Getting that wrong is not a cosmetic bug. If integration faults were filed as
target failures, Redbeak's own defects would show up as customer defects; if
target crashes were filed as adapter faults, a genuinely broken target would
quietly vanish from the denominator. So the mapping is explicit here, one stage
per exception class, and an exception this module does not recognise is
classified as ``adapter`` — never as a target failure. Silence is attributed to
us, not to the customer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, TypeVar

import redbeak_contracts as rc

#: The stages of ``common.schema.json#/$defs/execution_error``. Restated as a
#: ``Literal`` for type-checking only; the values are validated against the
#: contract itself in :meth:`ExecutionError.to_dict`.
ExecutionStage = Literal[
    "target_unavailable", "adapter", "transport", "timeout", "protocol", "runner"
]

#: The one stage attributable to the system under test rather than to Redbeak.
TARGET_STAGE: ExecutionStage = "target_unavailable"

#: ``execution_error.message`` is capped by the contract; a raw exception string
#: can be arbitrarily long, so normalisation truncates rather than failing
#: validation at the point where evidence is being recorded.
MAX_MESSAGE_LENGTH = 2000

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ExecutionError:
    """A contract-shaped ``execution_error``, validated on construction.

    Validating here rather than at serialisation time means an ill-formed error
    cannot be constructed, held, and then fail validation later while a case is
    already being completed.
    """

    stage: ExecutionStage
    code: str
    message: str
    retryable: bool | None = None

    def __post_init__(self) -> None:
        rc.validate_definition("common", "execution_error", self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
        }
        if self.retryable is not None:
            document["retryable"] = self.retryable
        return document

    @property
    def is_target_failure(self) -> bool:
        return self.stage == TARGET_STAGE


class AdapterError(Exception):
    """Root of the hierarchy: every failure this SDK knows how to classify.

    It is the *root*, not the adapter-stage member — catching it catches target
    failures too. :class:`AdapterInternalError` is the narrower class meaning
    "the adapter itself broke"; the two are deliberately not interchangeable,
    and :func:`is_target_failure` is how a caller tells them apart without
    having to know the class list.

    Subclasses carry their stage as class data, so the exception type alone
    determines attribution. A caller cannot pass a stage in and quietly relabel
    an integration fault as a target failure.
    """

    stage: ClassVar[ExecutionStage] = "adapter"
    default_code: ClassVar[str] = "adapter_failure"
    default_retryable: ClassVar[bool] = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        self.code = code or self.default_code
        self.retryable = self.default_retryable if retryable is None else retryable

    def to_execution_error(self) -> ExecutionError:
        return ExecutionError(
            stage=self.stage,
            code=self.code,
            message=_clip(str(self) or self.code),
            retryable=self.retryable,
        )


class AdapterInternalError(AdapterError):
    """The adapter itself failed: a bug, a bad fixture, an impossible request.

    Reported at the ``adapter`` stage, which the architecture keeps separate
    from target results. This is where an unrecognised exception lands.
    """

    stage = "adapter"
    default_code = "adapter_failure"


class AdapterConfigurationError(AdapterInternalError):
    """The adapter was constructed or driven in a way it cannot honour."""

    default_code = "adapter_misconfigured"


class HiddenDataError(AdapterInternalError):
    """Evaluator-private material reached an adapter input and was refused.

    Refusing loudly matters more than tolerating the payload: silently stripping
    the offending keys would let a leak keep happening while every run stayed
    green.
    """

    default_code = "hidden_data_rejected"


class UnknownSessionError(AdapterInternalError):
    """A call named a session that was never opened, or was already closed."""

    default_code = "unknown_session"


class AdapterProtocolError(AdapterError):
    """The adapter and Redbeak disagree about the shape of an exchange."""

    stage = "protocol"
    default_code = "adapter_protocol_violation"


class AdapterTransportError(AdapterError):
    """The adapter could not carry a call to the target."""

    stage = "transport"
    default_code = "adapter_transport_failure"
    default_retryable = True


class AdapterTimeoutError(AdapterError):
    """A call exceeded its deadline before the target answered."""

    stage = "timeout"
    default_code = "adapter_timeout"
    default_retryable = True


class TargetUnavailableError(AdapterError):
    """The system under test is unreachable or crashed.

    This is the only exception in the hierarchy attributable to the customer's
    system. Raise it when the target could not be reached or did not survive the
    call — never when it answered badly, which is a normal completed turn whose
    quality the evaluator judges.
    """

    stage = TARGET_STAGE
    default_code = "target_unavailable"
    default_retryable = True


def _clip(message: str) -> str:
    text = " ".join(message.split()) or "unspecified failure"
    if len(text) <= MAX_MESSAGE_LENGTH:
        return text
    return text[: MAX_MESSAGE_LENGTH - 1] + "…"


def normalize_exception(exc: BaseException) -> ExecutionError:
    """Classify any exception raised by adapter code.

    ``asyncio.CancelledError`` is re-raised rather than classified: cancellation
    is a control signal, not a case outcome, and recording it as an adapter
    failure would bury a cancelled run under fabricated errors.
    """
    if isinstance(exc, asyncio.CancelledError):
        raise exc
    if isinstance(exc, AdapterError):
        return exc.to_execution_error()
    if isinstance(exc, TimeoutError):
        return ExecutionError(
            stage="timeout",
            code=AdapterTimeoutError.default_code,
            message=_clip(f"{type(exc).__name__}: {exc}"),
            retryable=True,
        )
    return ExecutionError(
        stage="adapter",
        code="unhandled_adapter_exception",
        message=_clip(f"{type(exc).__name__}: {exc}"),
        retryable=False,
    )


def is_target_failure(value: BaseException | ExecutionError) -> bool:
    """Whether a failure is attributable to the system under test."""
    if isinstance(value, ExecutionError):
        return value.is_target_failure
    return isinstance(value, AdapterError) and value.stage == TARGET_STAGE


async def call_with_timeout(awaitable: Any, *, timeout_ms: int | None = None) -> Any:
    """Await ``awaitable``, raising :class:`AdapterTimeoutError` on expiry.

    Wrapping the standard timeout keeps the deadline in the same vocabulary as
    every other adapter failure, so a runner has one exception hierarchy to
    catch rather than two.
    """
    if timeout_ms is None:
        return await awaitable
    if timeout_ms <= 0:
        # This function takes ownership of the awaitable, so a refusal has to
        # close it; leaving it unawaited would emit a RuntimeWarning from
        # whichever unrelated line the garbage collector happened to run on.
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise AdapterConfigurationError(f"timeout_ms must be positive, got {timeout_ms}")
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout_ms / 1000)
    except TimeoutError as exc:
        raise AdapterTimeoutError(f"adapter call exceeded {timeout_ms} ms") from exc


__all__ = [
    "MAX_MESSAGE_LENGTH",
    "TARGET_STAGE",
    "AdapterConfigurationError",
    "AdapterError",
    "AdapterInternalError",
    "AdapterProtocolError",
    "AdapterTimeoutError",
    "AdapterTransportError",
    "ExecutionError",
    "ExecutionStage",
    "HiddenDataError",
    "TargetUnavailableError",
    "UnknownSessionError",
    "call_with_timeout",
    "is_target_failure",
    "normalize_exception",
]
