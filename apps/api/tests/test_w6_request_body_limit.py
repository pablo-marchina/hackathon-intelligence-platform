"""Limits at the ASGI boundary, not only a client-provided length header."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
from hackathon_api.body_limit import RequestBodyLimitMiddleware
from hackathon_api.main import app


def call_asgi(
    parts: list[bytes], headers: list[tuple[bytes, bytes]] | None = None, *, max_bytes: int = 6
) -> tuple[list[dict], int]:
    """Deliver explicitly framed ASGI chunks, avoiding HTTP client re-framing."""
    downstream_calls = 0

    async def downstream(scope, receive, send):
        nonlocal downstream_calls
        downstream_calls += 1
        collected = bytearray()
        while True:
            message = await receive()
            collected.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        payload = bytes(collected)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": payload})

    async def run() -> list[dict]:
        pending = [
            {"type": "http.request", "body": part, "more_body": i < len(parts) - 1}
            for i, part in enumerate(parts)
        ]
        if not pending:
            pending = [{"type": "http.request", "body": b"", "more_body": False}]

        async def receive():
            if pending:
                return pending.pop(0)
            return {"type": "http.disconnect"}

        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/demo", "headers": headers or []}
        await RequestBodyLimitMiddleware(downstream, max_bytes=max_bytes)(scope, receive, send)
        return sent

    return asyncio.run(run()), downstream_calls


def test_chunked_upload_over_limit_fails_before_route_or_database():
    result, calls = call_asgi([b"abc", b"def", b"g"])
    assert result[0]["status"] == 413
    assert json.loads(result[1]["body"])["detail"] == "request body too large"
    assert calls == 0


def test_lied_content_length_cannot_bypass_actual_limit():
    result, calls = call_asgi([b"abcdefg"], [(b"content-length", b"1")])
    assert result[0]["status"] == 413
    assert calls == 0


def test_exact_limit_replays_all_chunks_unchanged():
    result, calls = call_asgi([b"a", b"bc", b"def"])
    assert result[0]["status"] == 200
    assert result[1]["body"] == b"abcdef"
    assert calls == 1


def test_empty_body_and_out_of_band_scopes():
    result, calls = call_asgi([])
    assert result[0]["status"] == 200
    assert result[1]["body"] == b""
    assert calls == 1


def test_invalid_content_length_and_duplicate_headers_rejected():
    for headers in (
        [(b"content-length", b"not-a-number")],
        [(b"content-length", b"-1")],
        [(b"content-length", b"1"), (b"content-length", b"2")],
        [(b"content-length", b"1234567890123456789")],
    ):
        result, calls = call_asgi([b"a"], headers)
        assert result[0]["status"] in (400, 413)
        assert calls == 0


def test_real_application_rejects_oversize_without_length_header():
    client = TestClient(app)
    response = client.post(
        "/does-not-exist",
        # httpx treats an iterator as a streaming request with no Content-Length.
        content=iter([b"x" * 600_000, b"y" * 600_000]),
    )
    assert response.status_code == 413
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-request-id"]


def test_oversized_cross_origin_request_still_receives_cors_headers():
    response = TestClient(app).post(
        "/does-not-exist",
        headers={"origin": "http://localhost:3000"},
        content=iter([b"x" * 600_000, b"y" * 600_000]),
    )
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_invalid_host_rejected_before_reading_body():
    response = TestClient(app).post(
        "/does-not-exist", headers={"host": "evil.test"}, content=b"small"
    )
    assert response.status_code == 400


def test_real_application_remains_compatible_with_regular_routes():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.post("/does-not-exist", content=b"small").status_code == 404
