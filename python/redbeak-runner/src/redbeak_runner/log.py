"""Operational logging that refuses to print secrets or case content.

The architecture requires application logs to exclude bearer tokens, runner
secrets, setup payloads, scenario text, agent output, and observation values.
A filter is the backstop: even a careless ``logger.info(payload)`` should not
leak those fields, because the leak would otherwise only show up in a customer's
log aggregator.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"rbk_[A-Za-z0-9_-]+"),
    re.compile(r"lease_v1_[A-Za-z0-9_-]+"),
    re.compile(r"(?i)bearer\s+\S+"),
)

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "bearer",
        "client_secret",
        "content",
        "credential",
        "credentials",
        "lease_token",
        "observations",
        "output",
        "password",
        "payload",
        "private_key",
        "refresh_token",
        "runner_key",
        "secret",
        "service_key",
        "session_token",
        "setup",
        "value",
    }
)

REDACTED = "[redacted]"


def redact_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if str(key).lower() in _SENSITIVE_KEYS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value


class RedactingFilter(logging.Filter):
    """Rewrite log records so secrets and case content cannot leave the process."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.msg))
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(redact_value(arg) for arg in record.args)
            elif isinstance(record.args, dict):
                record.args = redact_value(record.args)
        for key, value in list(record.__dict__.items()):
            if key in _SENSITIVE_KEYS or str(key).lower() in _SENSITIVE_KEYS:
                setattr(record, key, REDACTED)
            elif key == "args":
                continue
            elif isinstance(value, (str, dict, list, tuple)):
                setattr(record, key, redact_value(value))
        return True


def configure_logging(*, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("redbeak.runner")
    logger.setLevel(level)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter())
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger("redbeak.runner")
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
