"""Structured logging + secret redaction.

Two guarantees from day one (mirrors the Rust core, IMPLEMENTATION_PLAN §22.1):
  1. Logs are JSON with an ``operation_id`` where relevant.
  2. Secrets — OAuth tokens, presigned URLs, SQL parameter values — never reach
     the log sink. The ``Secret`` wrapper makes that the default.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone


class Secret:
    """A value that must never be logged, printed, or serialized in the clear.

    ``str``/``repr`` render ``***``. Read the real value only via ``.expose()``,
    which is deliberately explicit so call sites are easy to review.
    """

    __slots__ = ("_v",)

    def __init__(self, value: str) -> None:
        self._v = value

    def expose(self) -> str:
        return self._v

    def __bool__(self) -> bool:
        return bool(self._v)

    def __str__(self) -> str:
        return "***"

    def __repr__(self) -> str:
        return "Secret(***)"


_BEARER = re.compile(r"(?i)\bbearer\s+\S+")
_PRESIGNED = re.compile(r"https://\S*(?:x-amz-|sig=|token=)\S*")
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9._-]{40,}\b")


def scrub(text: str) -> str:
    """Best-effort redaction of bearer tokens / presigned URLs / long opaque
    tokens. Backstop only — the primary defense is not passing secrets in."""
    text = _BEARER.sub("bearer ***", text)
    text = _PRESIGNED.sub("***", text)
    text = _LONG_TOKEN.sub("***", text)
    return text


def new_operation_id() -> str:
    return str(uuid.uuid4())


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "target": record.name,
        }
        for key, val in getattr(record, "fields", {}).items():
            payload[key] = val
        return json.dumps(payload)


_configured = False


def init() -> None:
    """Install the JSON handler once. Idempotent."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger("pyjama")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
    _configured = True


def log(message: str, **fields) -> None:
    """Emit a structured info line. Callers pass safe fields only."""
    logging.getLogger("pyjama").info(message, extra={"fields": fields})
