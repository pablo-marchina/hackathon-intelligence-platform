import logging

from hackathon_shared.logging import JsonFormatter, redact


def test_credentials_redacted(monkeypatch):
    monkeypatch.setenv("ADMIN_API_KEY", "eightsecretcharacters")
    value = "Authorization: Bearer abcdefghi password=supersecret postgresql://user:p4ss@db.dev:5432/a https://a/?token=secret123"
    out = redact(value + " eightsecretcharacters")
    for secret in ("abcdefghi", "supersecret", "p4ss", "secret123", "eightsecretcharacters"):
        assert secret not in out
    assert out.count("[REDACTED]") >= 5


def test_exception_redaction():
    try:
        raise ValueError("password=privatevalue")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "hip", logging.ERROR, __file__, 1, "failure token=abc", (), sys.exc_info()
        )
    result = JsonFormatter().format(record)
    assert "privatevalue" not in result
    assert "[REDACTED]" in result
