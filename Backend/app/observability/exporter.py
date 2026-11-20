"""The metrics sidecar: a separate ASGI app that aggregates the multiprocess files of PROMETHEUS_MULTIPROC_DIR.

Run with `python -m Backend.app.observability.exporter` (port EXPORTER_PORT, default 9102). It is not part of the backend app: no route
policy applies, and it is published on no host port and behind no Caddy route.
"""
import os


async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    if scope["path"] != "/metrics" or scope["method"] not in ("GET", "HEAD"):
        await _respond(send, 404, b"not found\n", "text/plain; charset=utf-8")
        return
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
        from prometheus_client.multiprocess import MultiProcessCollector

        registry = CollectorRegistry()  # fresh per scrape, as the multiprocess collector requires
        MultiProcessCollector(registry)
        body = generate_latest(registry)
    except Exception:
        await _respond(send, 500, b"metrics unavailable\n", "text/plain; charset=utf-8")
        return
    await _respond(send, 200, body, CONTENT_TYPE_LATEST)


async def _respond(send, status: int, body: bytes, content_type: str):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.environ.get("EXPORTER_HOST", "0.0.0.0"), port=int(os.environ.get("EXPORTER_PORT", "9102")),
                log_level="warning")
