"""Adapter failures stay distinguishable from failures of the system under test.

The property under test throughout: exactly one exception class maps to
``target_unavailable``, and nothing reaches that stage by accident. An
unrecognised exception is attributed to Redbeak, because attributing our own
bugs to a customer's system is the more damaging error of the two.
"""

from __future__ import annotations

import asyncio

import pytest
import redbeak_contracts as rc
from _sdk_fixtures import SCENARIO_ID, run
from jsonschema.exceptions import ValidationError
from redbeak_adapter_sdk import (
    MAX_MESSAGE_LENGTH,
    AdapterConfigurationError,
    AdapterError,
    AdapterInternalError,
    AdapterProtocolError,
    AdapterTimeoutError,
    AdapterTransportError,
    ExecutionError,
    HiddenDataError,
    TargetUnavailableError,
    UnknownSessionError,
    call_with_timeout,
    is_target_failure,
    normalize_exception,
)

STAGE_BY_EXCEPTION = [
    (AdapterInternalError, "adapter"),
    (AdapterConfigurationError, "adapter"),
    (HiddenDataError, "adapter"),
    (UnknownSessionError, "adapter"),
    (AdapterProtocolError, "protocol"),
    (AdapterTransportError, "transport"),
    (AdapterTimeoutError, "timeout"),
    (TargetUnavailableError, "target_unavailable"),
]


class TestStageMapping:
    @pytest.mark.parametrize(("exception_class", "stage"), STAGE_BY_EXCEPTION)
    def test_each_exception_carries_its_stage(
        self, exception_class: type[AdapterError], stage: str
    ) -> None:
        assert normalize_exception(exception_class("boom")).stage == stage

    def test_only_one_class_is_attributed_to_the_target(self) -> None:
        target_classes = [cls for cls, stage in STAGE_BY_EXCEPTION if stage == "target_unavailable"]
        assert target_classes == [TargetUnavailableError]

    def test_target_unavailable_is_not_an_adapter_failure(self) -> None:
        """Sibling classes, not parent and child: a catch-all for adapter faults
        must not sweep up a target failure."""
        assert not issubclass(TargetUnavailableError, AdapterInternalError)
        assert issubclass(TargetUnavailableError, AdapterError)


class TestNormalisingUnknownExceptions:
    def test_an_unrecognised_exception_is_ours_not_the_targets(self) -> None:
        error = normalize_exception(RuntimeError("something odd"))
        assert error.stage == "adapter"
        assert error.code == "unhandled_adapter_exception"
        assert not is_target_failure(error)

    def test_a_key_error_inside_adapter_code_is_ours(self) -> None:
        assert normalize_exception(KeyError("missing")).stage == "adapter"

    def test_a_builtin_timeout_becomes_a_timeout_stage(self) -> None:
        assert normalize_exception(TimeoutError("slow")).stage == "timeout"

    def test_cancellation_is_re_raised_not_classified(self) -> None:
        """A cancelled run must not turn into a pile of invented adapter errors."""
        with pytest.raises(asyncio.CancelledError):
            normalize_exception(asyncio.CancelledError())

    def test_the_exception_type_survives_into_the_message(self) -> None:
        assert "RuntimeError" in normalize_exception(RuntimeError("boom")).message

    def test_a_very_long_message_is_clipped_to_what_the_contract_allows(self) -> None:
        error = normalize_exception(RuntimeError("x" * 10_000))
        assert len(error.message) <= MAX_MESSAGE_LENGTH

    def test_an_empty_message_still_validates(self) -> None:
        assert normalize_exception(RuntimeError()).message


class TestExecutionError:
    def test_validates_against_the_contract_on_construction(self) -> None:
        error = ExecutionError(stage="adapter", code="adapter_failure", message="boom")
        rc.validate_definition("common", "execution_error", error.to_dict())

    def test_refuses_a_code_the_contract_would_not_accept(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionError(stage="adapter", code="Not A Slug", message="boom")

    def test_refuses_a_stage_the_contract_does_not_define(self) -> None:
        with pytest.raises(ValidationError):
            ExecutionError(stage="model_was_rude", code="x", message="boom")  # type: ignore[arg-type]

    def test_fits_a_case_completion(self) -> None:
        completion = {
            "schema_version": rc.CONTRACT_VERSION,
            "lease_token": "x" * 32,
            "idempotency_key": SCENARIO_ID,
            "status": "execution_failed",
            "observations": [],
            "error": TargetUnavailableError("target crashed").to_execution_error().to_dict(),
            "operational": {"duration_ms": 5},
        }
        rc.validate("case-completion", completion)


class TestRetryability:
    @pytest.mark.parametrize(
        ("exception_class", "retryable"),
        [
            (AdapterInternalError, False),
            (AdapterProtocolError, False),
            (AdapterTransportError, True),
            (AdapterTimeoutError, True),
            (TargetUnavailableError, True),
        ],
    )
    def test_defaults_match_whether_retrying_could_help(
        self, exception_class: type[AdapterError], retryable: bool
    ) -> None:
        assert normalize_exception(exception_class("boom")).retryable is retryable

    def test_a_caller_can_override_the_default(self) -> None:
        error = normalize_exception(AdapterTransportError("permanent", retryable=False))
        assert error.retryable is False


class TestCallWithTimeout:
    def test_returns_the_value_when_there_is_no_deadline(self) -> None:
        async def answer() -> str:
            return "done"

        assert run(call_with_timeout(answer())) == "done"

    def test_raises_an_adapter_timeout_rather_than_a_builtin(self) -> None:
        async def never() -> None:
            await asyncio.sleep(10)

        async def attempt() -> None:
            await call_with_timeout(never(), timeout_ms=10)

        with pytest.raises(AdapterTimeoutError):
            run(attempt())

    def test_a_timeout_is_not_a_target_failure(self) -> None:
        """A slow adapter is an integration problem until proven otherwise."""
        assert not is_target_failure(AdapterTimeoutError("slow"))

    def test_refuses_a_nonsensical_deadline(self) -> None:
        async def answer() -> str:
            return "done"

        async def attempt() -> None:
            await call_with_timeout(answer(), timeout_ms=0)

        with pytest.raises(AdapterConfigurationError):
            run(attempt())
