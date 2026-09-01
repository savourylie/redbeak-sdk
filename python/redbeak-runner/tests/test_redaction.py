"""Operational logs must not contain runner secrets or case content."""

from __future__ import annotations

import pytest
from redbeak_runner.log import REDACTED, configure_logging, redact_value


def test_redacting_filter_strips_keys_tokens_and_payloads(
    capsys: pytest.CaptureFixture[str],
) -> None:
    logger = configure_logging()
    secret = "rbk_live_this_is_a_synthetic_runner_key_value"
    token = "lease_v1_abcdefghijklmnopqrstuvwx"
    logger.info("started with %s and %s", secret, token)
    logger.info(
        "assignment",
        extra={
            "setup": {"payload": {"password": "hunter2"}},
            "content": "Archive the ideas file",
            "lease_token": token,
        },
    )
    text = capsys.readouterr().err
    assert secret not in text
    assert token not in text
    assert "hunter2" not in text
    assert "Archive the ideas file" not in text
    assert REDACTED in text


def test_redact_value_walks_nested_documents() -> None:
    redacted = redact_value(
        {
            "lease_token": "lease_v1_abcdefghijklmnopqrstuvwx",
            "output": {"type": "agent_message", "content": "secret-answer"},
            "observations": [{"name": "note_state", "value": "hidden"}],
            "ok": "visible",
        }
    )
    assert redacted["ok"] == "visible"
    assert redacted["lease_token"] == REDACTED
    assert redacted["output"] == REDACTED
    assert redacted["observations"] == REDACTED
