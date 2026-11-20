"""Page-visit beacon (observability O4a): the one write route of the metrics feature that is reachable from the internet.

The body is {"client": "web"|"android", "page": <route template>}. It is read raw and validated by hand so that no error body ever
echoes it; a value outside the allowlist is counted as "other", and nothing from the body is logged or stored. The body is capped at
1 KB by BodyLimitMiddleware and the route is rate limited per client (`abuse.telemetry_per_min`, 0 = off)."""
import json

from fastapi import Request, Response
from starlette.requests import ClientDisconnect

from Backend.app import observability
from Backend.app.services.abuse import enforce
from Backend.app.stage_policy import make_router

router = make_router(prefix="/telemetry", tags=["telemetry"])


@router.post("/pageview", status_code=204, response_class=Response)
async def pageview(request: Request) -> Response:
    enforce("telemetry", request)   # 429; the refusal is counted as RATE_LIMITED{telemetry} by the limiter itself
    try:
        data = json.loads(await request.body())
    except ClientDisconnect:   # BodyLimitMiddleware cut a body over its cap short and replaces this answer with its 413
        return Response(status_code=413)
    except (ValueError, RecursionError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("client"), str) or not isinstance(data.get("page"), str):
        return Response(status_code=422, content=b'{"detail":"Invalid beacon"}', media_type="application/json")
    if observability.enabled():   # the metrics module is not imported at all while metrics are off
        from Backend.app.observability.metrics import record_pageview
        record_pageview(data["client"], data["page"])
    return Response(status_code=204)
