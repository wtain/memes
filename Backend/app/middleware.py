"""Pure-ASGI middlewares of the backend (no BaseHTTPMiddleware: they must see the raw `receive` stream).

ClientContextMiddleware: puts the client address into a context variable (Backend/app/services/abuse.py).
BodyLimitMiddleware: charges the per-client bug-report rate limit and bounds the request body of /api/bug-reports before any
multipart parsing (P5a-5).
"""
import json

from Backend.app.services.abuse import (BUG_REPORT_CHARGED, UNKNOWN_CLIENT, allow_client, bug_report_body_limit_bytes,
                                        current_client, normalize_client)

BUG_REPORT_PATH = "/api/bug-reports"


class ClientContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        token = current_client.set(normalize_client(client[0]) if client else UNKNOWN_CLIENT)
        try:
            await self.app(scope, receive, send)
        finally:
            current_client.reset(token)


class BodyLimitMiddleware:
    """Answers 429 (Retry-After: 60) to a POST to /api/bug-reports from a client over `abuse.bug_reports_per_min`, without reading the
    body; otherwise the request has been charged one token and the endpoint does not charge another (scope flag). Then answers 413 for a POST to /api/bug-reports whose body exceeds `bug_reports.max_body_mb`: at once when Content-Length
    already says so, otherwise as soon as the bytes counted from `receive` pass the limit. The application, and so the multipart
    parser, never sees more than the limit. A limit of 0 switches the middleware off (the developer default)."""

    def __init__(self, app, path: str = BUG_REPORT_PATH):
        self.app = app
        self.path = path

    async def __call__(self, scope, receive, send):
        limit = 0
        if scope["type"] == "http" and scope["path"] == self.path and scope["method"] == "POST":
            client = scope.get("client")
            if not allow_client("bug_reports", client[0] if client else None):
                await _respond_429(send)
                return
            scope[BUG_REPORT_CHARGED] = True
            limit = bug_report_body_limit_bytes()
        if not limit:
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    await _respond_413(send)
                    return
            except ValueError:
                pass  # a malformed length is left to the server; the byte count below still applies
        seen = 0
        exceeded = False
        answered = False

        async def counting_receive():
            nonlocal seen, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # Do not raise: FastAPI turns an exception from the body stream into its own 400. The parser is told the
                    # client went away (it stops, having been given at most `limit` bytes) and the answer is replaced below.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def replacing_send(message):
            nonlocal answered
            if exceeded:
                if not answered:
                    answered = True
                    await _respond_413(send)
                return
            await send(message)

        await self.app(scope, counting_receive, replacing_send)
        if exceeded and not answered:
            await _respond_413(send)


async def _respond_413(send) -> None:
    body = json.dumps({"detail": "Request body too large"}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})


async def _respond_429(send) -> None:
    body = json.dumps({"detail": "Too many requests, try again in a minute"}).encode()
    await send({"type": "http.response.start", "status": 429,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"retry-after", b"60"), (b"connection", b"close")]})
    await send({"type": "http.response.body", "body": body})
