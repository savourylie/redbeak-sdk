"""UUID and token helpers that match contract patterns."""

from __future__ import annotations

import secrets
import uuid


def new_uuid() -> str:
    return str(uuid.uuid4())


def new_lease_token() -> str:
    return "lease_v1_" + secrets.token_urlsafe(24)


def new_runner_key() -> str:
    return "rbk_live_" + secrets.token_urlsafe(32)
