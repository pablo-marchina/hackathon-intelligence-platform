"""W6 iteration 14: authenticated SMTP must use verified, non-downgradable TLS."""
from __future__ import annotations

import asyncio
import smtplib
import ssl

import pytest
from pydantic import ValidationError

from hackathon_alerts.delivery import SmtpEmailSender, render_alert_email
from hackathon_shared.settings import Settings


class FakeSMTP:
    calls: list[tuple[str, object]] = []

    def __init__(self, host: str, port: int, **kwargs: object) -> None:
        self.calls.append(("connect", (host, port, kwargs)))

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_args: object) -> None:
        self.calls.append(("close", None))

    def starttls(self, *, context: ssl.SSLContext) -> None:
        self.calls.append(("starttls", context))

    def ehlo(self) -> None:
        self.calls.append(("ehlo", None))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", (username, password)))

    def send_message(self, message: object) -> None:
        self.calls.append(("send", message))


def send(sender: SmtpEmailSender, key: str = "alert-123") -> None:
    asyncio.run(sender.send(
        to="user@example.test", subject="Test", text="Body", idempotency_key=key,
    ))


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.calls = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def test_starttls_validates_certificate_before_authentication(fake_smtp: type[FakeSMTP]) -> None:
    sender = SmtpEmailSender(
        host="mail.example.test", port=587, from_address="alerts@example.test",
        username="account", password="secret", starttls=True,
    )
    send(sender)
    names = [key for key, _ in fake_smtp.calls]
    assert names == ["connect", "starttls", "ehlo", "login", "send", "close"]
    context = fake_smtp.calls[1][1]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    message = fake_smtp.calls[4][1]
    assert message["X-Idempotency-Key"] == "alert-123"
    assert message["Message-ID"] == "<alert-123@hackathon-intelligence.local>"


def test_implicit_tls_uses_verifying_sslcontext(fake_smtp: type[FakeSMTP]) -> None:
    sender = SmtpEmailSender(
        host="mail.example.test", port=465, from_address="alerts@example.test",
        ssl_enabled=True, username="account", password="secret",
    )
    send(sender)
    names = [key for key, _ in fake_smtp.calls]
    assert names == ["connect", "login", "send", "close"]
    options = fake_smtp.calls[0][1][2]
    assert options["timeout"] == 15.0
    assert options["context"].check_hostname is True
    assert options["context"].verify_mode == ssl.CERT_REQUIRED


def test_local_anonymous_smtp_still_supported(fake_smtp: type[FakeSMTP]) -> None:
    sender = SmtpEmailSender(host="127.0.0.1", port=1025, from_address="test@example.test")
    send(sender)
    assert [name for name, _ in fake_smtp.calls] == ["connect", "send", "close"]


@pytest.mark.parametrize("args", [
    {"username": "alice", "password": "pw"},
    {"password": "pw"},
    {"starttls": True, "ssl_enabled": True},
    {"timeout": 0},
    {"timeout": 121},
])
def test_insecure_configuration_rejected_before_network(args: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SmtpEmailSender(host="mail.example.test", port=587,
                        from_address="alerts@example.test", **args)


@pytest.mark.parametrize("config", [
    {"smtp_username": "u", "smtp_password": "pw"},
    {"smtp_password": "pw"},
    {"smtp_starttls": True, "smtp_ssl": True},
])
def test_application_settings_reject_insecure_smtp(config: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **config)


def test_application_settings_support_verified_implicit_tls() -> None:
    settings = Settings(
        _env_file=None, smtp_ssl=True, smtp_username="alice", smtp_password="secret",
    )
    assert settings.smtp_ssl and not settings.smtp_starttls


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_live_alert_dispatch_rejects_plaintext_smtp(environment: str) -> None:
    with pytest.raises(ValidationError, match="requires verified SMTP TLS"):
        Settings(_env_file=None, app_env=environment, alert_dispatch_enabled=True)


def test_disabled_alert_dispatch_allows_local_plaintext_relay() -> None:
    settings = Settings(_env_file=None, app_env="production", alert_dispatch_enabled=False)
    assert not settings.smtp_starttls and not settings.smtp_ssl


def test_starttls_failure_does_not_authenticate_or_send(
    fake_smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_starttls(self: FakeSMTP, *, context: ssl.SSLContext) -> None:
        self.calls.append(("starttls-failed", context))
        raise smtplib.SMTPNotSupportedError("STARTTLS is unavailable")

    monkeypatch.setattr(FakeSMTP, "starttls", fail_starttls)
    sender = SmtpEmailSender(
        host="mail.example.test", port=587, from_address="alerts@example.test",
        starttls=True, username="alice", password="pw",
    )
    with pytest.raises(smtplib.SMTPNotSupportedError):
        send(sender)
    assert not {"login", "send"}.intersection(name for name, _ in fake_smtp.calls)


@pytest.mark.parametrize("key", ["", "a\nb", "<bad>", "x" * 129, "with spaces"])
def test_invalid_message_id_is_rejected_before_network(
    fake_smtp: type[FakeSMTP], key: str,
) -> None:
    sender = SmtpEmailSender(host="localhost", port=1025, from_address="a@example.test")
    with pytest.raises(ValueError, match="idempotency key"):
        send(sender, key)
    assert fake_smtp.calls == []


def test_external_event_names_cannot_break_subject_header() -> None:
    subject, body = render_alert_email(
        alert_type="new_match", event_name="Hackathon\r\nBcc: someone@example.test",
        event_url="https://example.test/events/1", payload={},
    )
    assert "\r" not in subject and "\n" not in subject
    assert "Hackathon Bcc: someone@example.test" in subject
    assert "https://example.test/events/1" in body
