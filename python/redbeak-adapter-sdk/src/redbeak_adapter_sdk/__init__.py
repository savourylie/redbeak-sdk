"""Redbeak target-adapter SDK.

The customer-side half of the Redbeak boundary: an asynchronous ``TargetAdapter``
protocol, the typed values that cross it, session state that cannot leak between
cases, and a failure vocabulary that keeps integration faults distinguishable
from failures of the system under test.

The SDK is deliberately ignorant of evaluation. It has no field for ground
truth, expected outcomes, rubrics, scoring thresholds, or the turns that come
next, and it validates its inputs against the frozen contract to keep it that
way even when the payload did not arrive through a validated envelope.

Nothing here reaches the network or requires a credential.
"""

from __future__ import annotations

import redbeak_contracts as rc

from redbeak_adapter_sdk.errors import (
    MAX_MESSAGE_LENGTH,
    TARGET_STAGE,
    AdapterConfigurationError,
    AdapterError,
    AdapterInternalError,
    AdapterProtocolError,
    AdapterTimeoutError,
    AdapterTransportError,
    ExecutionError,
    ExecutionStage,
    HiddenDataError,
    TargetUnavailableError,
    UnknownSessionError,
    call_with_timeout,
    is_target_failure,
    normalize_exception,
)
from redbeak_adapter_sdk.guards import (
    VERDICT_LIKE_PROPERTY_NAMES,
    assert_no_hidden_data,
    find_hidden_data,
    hidden_property_names,
)
from redbeak_adapter_sdk.models import (
    AdapterCapabilities,
    AgentOutput,
    ArtifactRef,
    Observation,
    ObservationRequest,
    ObservationSource,
    SessionContext,
    UserInput,
    observations_to_wire,
)
from redbeak_adapter_sdk.protocol import TargetAdapter
from redbeak_adapter_sdk.sessions import SessionStore
from redbeak_adapter_sdk.single_turn import SingleTurnTextAdapter

#: The contract this SDK speaks. Read from the loader so an SDK built against a
#: newer contract cannot silently keep claiming the old one.
CONTRACT_VERSION = rc.CONTRACT_VERSION

SDK_VERSION = "0.1.0"

__all__ = [
    "CONTRACT_VERSION",
    "MAX_MESSAGE_LENGTH",
    "SDK_VERSION",
    "TARGET_STAGE",
    "VERDICT_LIKE_PROPERTY_NAMES",
    "AdapterCapabilities",
    "AdapterConfigurationError",
    "AdapterError",
    "AdapterInternalError",
    "AdapterProtocolError",
    "AdapterTimeoutError",
    "AdapterTransportError",
    "AgentOutput",
    "ArtifactRef",
    "ExecutionError",
    "ExecutionStage",
    "HiddenDataError",
    "Observation",
    "ObservationRequest",
    "ObservationSource",
    "SessionContext",
    "SessionStore",
    "SingleTurnTextAdapter",
    "TargetAdapter",
    "TargetUnavailableError",
    "UnknownSessionError",
    "UserInput",
    "assert_no_hidden_data",
    "call_with_timeout",
    "find_hidden_data",
    "hidden_property_names",
    "is_target_failure",
    "normalize_exception",
    "observations_to_wire",
]
