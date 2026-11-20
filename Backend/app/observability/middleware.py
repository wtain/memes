"""MetricsMiddleware: pure ASGI request counter, duration histogram and in-flight gauge.

A failure of the metrics code (a full multiprocess directory, a broken value file) is swallowed: it never changes a response.
"""
import time

from Backend.app.observability import metrics


class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
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
