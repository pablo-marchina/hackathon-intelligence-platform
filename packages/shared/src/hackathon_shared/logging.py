from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r'(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;"\']+'),
    re.compile(
        r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|client_secret|database_url)\s*[:=]\s*)[^\s,;"\']+'
    ),
    re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s/@]+:)[^@\s]+(@)"),
    re.compile(r"(?i)([?&](?:token|api_key|access_token|password|secret)=)[^&\s]+"),
)


def redact(text: str) -> str:
    """Remove common credential forms and explicitly supplied secrets from logging output."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]" + m.group(2), text)
        else:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
    # Environment-supplied secrets are redacted even if they lack a recognizable prefix.
    for name in (
        "DATABASE_URL",
        "ADMIN_API_KEY",
        "OAUTH_CLIENT_SECRET",
        "SMTP_PASSWORD",
        "AWS_SECRET_ACCESS_KEY",
    ):
        secret = os.environ.get(name, "")
        if secret and len(secret) >= 8:
            text = text.replace(secret, "[REDACTED]")
    return text


class JsonFormatter(logging.Formatter):
    """Structured logs with redaction of messages, metadata and exception traces."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key in ("request_id", "job_id", "source_id", "run_id", "path", "method"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact(str(value))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
