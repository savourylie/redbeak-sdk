"""Minimal Redbeak target adapter for an OpenAI-compatible LLM API.

``redbeak runs create --adapter redbeak_example_llm_api ...`` finds
:func:`create_adapter` here. See ``README.md`` for the whole customer flow.
"""

from redbeak_example_llm_api.adapter import (
    ADAPTER_VERSION,
    DEFAULT_TIMEOUT_S,
    LlmApiAdapter,
    create_adapter,
)

__all__ = ["ADAPTER_VERSION", "DEFAULT_TIMEOUT_S", "LlmApiAdapter", "create_adapter"]
