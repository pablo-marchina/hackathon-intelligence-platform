from __future__ import annotations

import asyncio
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Protocol


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, text: str, idempotency_key: str) -> str: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay_seconds: int = 60
    multiplier: int = 4

    def delay_seconds(self, attempt_count: int) -> int | None:
        if attempt_count >= self.max_attempts:
            return None
        exponent = max(0, attempt_count - 1)
        return min(86_400, self.base_delay_seconds * (self.multiplier**exponent))


class SmtpEmailSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_address: str,
        username: str | None = None,
        password: str | None = None,
        starttls: bool = False,
        ssl_enabled: bool = False,
        timeout: float = 15.0,
    ) -> None:
        if starttls and ssl_enabled:
            raise ValueError("SMTP STARTTLS and implicit TLS are mutually exclusive")
        if username and not (starttls or ssl_enabled):
            raise ValueError("SMTP authentication requires verified TLS")
        if password and not username:
            raise ValueError("SMTP password requires a username")
        if not 0 < timeout <= 120:
            raise ValueError("invalid SMTP timeout")
        self.host = host
        self.port = port
        self.from_address = from_address
        self.username = username
        self.password = password
        self.starttls = starttls
        self.ssl_enabled = ssl_enabled
        self.timeout = timeout

    async def send(self, *, to: str, subject: str, text: str, idempotency_key: str) -> str:
        # A predictable RFC-compatible message ID avoids malformed headers when
        # a caller inadvertently supplies a free-form idempotency key.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", idempotency_key):
            raise ValueError("invalid email idempotency key")
        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = to
        message["Subject"] = subject
        message["Message-ID"] = f"<{idempotency_key}@hackathon-intelligence.local>"
        message["X-Idempotency-Key"] = idempotency_key
        message.set_content(text)

        def _send() -> None:
            # The smtplib default STARTTLS context is not guaranteed to verify
            # the server certificate. Always pass a verifying context (also for
            # implicit TLS on port 465); never downgrade authenticated sessions.
            secure_context = ssl.create_default_context()
            client_connection: smtplib.SMTP = (
                smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=secure_context)
                if self.ssl_enabled
                else smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            )
            with client_connection as client:
                if self.starttls:
                    client.starttls(context=secure_context)
                    client.ehlo()
                if self.username:
                    client.login(self.username, self.password or "")
                client.send_message(message)

        await asyncio.to_thread(_send)
        return idempotency_key


def render_alert_email(
    *,
    alert_type: str,
    event_name: str,
    event_url: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    # Event names originate from external sources; preserve the meaning without
    # allowing a newline to break email header construction or defer delivery.
    safe_name = " ".join(str(event_name).splitlines()).strip()
    subject = f"{safe_name}: {alert_type.replace('_', ' ')}"
    lines = [event_name, "", f"Update: {alert_type.replace('_', ' ')}"]
    field = payload.get("field_path") or payload.get("changed_field")
    if field:
        lines.append(f"Changed field: {field}")
    deadline_local = payload.get("deadline_local") or payload.get("deadline")
    if deadline_local:
        lines.append(f"Deadline: {deadline_local}")
    if payload.get("timezone"):
        lines.append(f"Timezone: {payload['timezone']}")
    if payload.get("old_value") is not None or payload.get("new_value") is not None:
        lines.append(f"Previous: {payload.get('old_value')}")
        lines.append(f"New: {payload.get('new_value')}")
    lines.extend(["", f"Open event: {event_url}"])
    return subject, "\n".join(lines)
