"""MetricsMiddleware: pure ASGI request counter, duration histogram and in-flight gauge.
RequestIdMiddleware and SlowRequestMiddleware: request id, unhandled-exception line, slow-request line (design section 6).

A failure of the metrics code (a full multiprocess directory, a broken value file) is swallowed: it never changes a response.
The two logging middlewares do not depend on `observability.metrics.enabled`.
"""
import json
import logging
import time
import uuid

from Backend.app.observability.logfiles import request_id_var

REQUEST_ID_KEY = "memes.request_id"
REQUEST_ID_HEADER = b"x-request-id"
logger = logging.getLogger("Backend.app.observability.requests")


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from Backend.app.observability import metrics  # lazy: the module is not imported while metrics are off
        started = time.perf_counter()
        status = {"code": 500}  # an exception before the response starts is a 500 from ServerErrorMiddleware, which sits outside
        in_flight = None
        try:
            in_flight = metrics.HTTP_IN_FLIGHT
            in_flight.inc()
        except Exception:
            in_flight = None

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                route = metrics.route_label(scope)
                method = metrics.method_label(scope.get("method"))
                metrics.HTTP_REQUESTS.labels(route, method, metrics.status_class(status["code"])).inc()
                metrics.HTTP_DURATION.labels(route, method).observe(time.perf_counter() - started)
            except Exception:
                pass
            if in_flight is not None:
                try:
                    in_flight.dec()
                except Exception:
                    pass


class RequestIdMiddleware:
    """Gives every HTTP request a server-generated id (uuid4 hex; a client X-Request-ID is ignored), returns it as the response header
    and makes it available to log lines (contextvar) and to other middleware (`scope["memes.request_id"]`).

    It is also the app's unhandled-exception boundary: an exception that escapes a route is logged as its class name, the route
    template and the request id (never the message, which for a database error can carry the bound search text), and answered with a
    fixed 500. Without it uvicorn would log the full traceback with the message."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex
        scope[REQUEST_ID_KEY] = request_id
        token = request_id_var.set(request_id)
        started = {"value": False}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                started["value"] = True
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != REQUEST_ID_HEADER]
                headers.append((REQUEST_ID_HEADER, request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as error:
            try:
                from Backend.app.observability import metrics
                logger.error("unhandled exception type=%s route=%s request_id=%s", type(error).__name__,
                             metrics.route_label(scope), request_id)
            except Exception:
                pass
            if not started["value"]:
                body = json.dumps({"detail": "Internal Server Error"}).encode()
                await send_wrapper({"type": "http.response.start", "status": 500,
                                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            request_id_var.reset(token)


def slow_request_ms() -> float:
    """`observability.slow_request_ms`; 0, unset or invalid means off. Never raises."""
    try:
        from config.settings import settings
        value = float(settings.get("observability.slow_request_ms", 0) or 0)
        return value if value > 0 else 0.0
    except Exception:
        return 0.0


class SlowRequestMiddleware:
    """One line per request slower than `observability.slow_request_ms`: route template (never the path or query), status, duration and
    request id. Sits inside RequestIdMiddleware."""

    def __init__(self, app, threshold_ms: float | None = None):
        self.app = app
        self.threshold_ms = threshold_ms

    async def __call__(self, scope, receive, send):
        threshold = slow_request_ms() if self.threshold_ms is None else self.threshold_ms
        if scope["type"] != "http" or not threshold:
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = {"code": 500}  # an exception before the response starts ends as a 500

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            try:
                duration_ms = (time.perf_counter() - started) * 1000
                if duration_ms >= threshold:
                    from Backend.app.observability import metrics
                    logger.warning("slow request route=%s status=%d duration_ms=%d request_id=%s", metrics.route_label(scope),
                                   status["code"], round(duration_ms), scope.get(REQUEST_ID_KEY, "-"))
            except Exception:
                pass
