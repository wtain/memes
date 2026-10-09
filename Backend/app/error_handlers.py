"""App-level error handlers."""
from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PRIVATE_PREFIX = "/api/meme-from-text"


def sanitised_errors(errors) -> list[dict]:
    """Keep only loc, msg and type: the default `input` (and `ctx`) would echo the user's message text."""
    return [{"loc": list(error.get("loc", ())), "msg": str(error.get("msg", "")), "type": str(error.get("type", ""))}
            for error in errors]


async def validation_error_handler(request: Request, exc: RequestValidationError):
    """422 without the rejected input for the meme-from-text routes; FastAPI's default everywhere else."""
    path = request.url.path
    if path == PRIVATE_PREFIX or path.startswith(PRIVATE_PREFIX + "/"):
        return JSONResponse(status_code=422, content={"detail": sanitised_errors(exc.errors())})
    return await request_validation_exception_handler(request, exc)
