"""ASGI-level request body limit, including chunked and streaming requests.

A Content-Length check alone is insufficient: HTTP clients can omit or lie
about the advertised size. Buffer at most the configured limit before entering
any route or DB transaction, then replay the original body to the downstream
ASGI application. Intended for the API's small JSON request payloads.
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_bytes: int):
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        lengths = [
            value for key, value in scope.get("headers", []) if key.lower() == b"content-length"
        ]
        if lengths:
            try:
                # Duplicate Content-Length fields and non-decimal values are invalid.
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                    raise ValueError("invalid content-length")
                declared = int(lengths[0])
            except (ValueError, UnicodeError):
                await JSONResponse({"detail": "invalid content-length"}, status_code=400)(
                    scope, receive, send,
                )
                return
            if declared > self.max_bytes:
                await JSONResponse({"detail": "request body too large"}, status_code=413)(
                    scope, receive, send,
                )
                return

        # Check actual bytes as they arrive; never allow an unbounded request
        # into JSON parsing, authentication middleware, or a DB-backed route.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # The peer went away; nothing downstream should run.
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(chunk) > self.max_bytes - len(body):
                await JSONResponse({"detail": "request body too large"}, status_code=413)(
                    scope, receive, send,
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            # Preserve ASGI disconnect semantics for streaming responses and
            # middleware that checks connection liveness after parsing input.
            return await receive()

        await self.app(scope, replay, send)
