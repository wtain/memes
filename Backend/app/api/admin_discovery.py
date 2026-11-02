import logging
from typing import AsyncGenerator, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.services.cache import no_cache_headers
from Backend.app.services.discovery_report_service import DiscoveryReportService
from Backend.app.types.generated.discoverymemes import Schema as DiscoveryMemes
from Backend.app.types.generated.discoverymisses import Schema as DiscoveryMisses
from Backend.app.types.generated.discoveryqueries import Schema as DiscoveryQueries
from repository.discovery_report import DEFAULT_SOURCES
from repository.meme_impressions import window_days
from Storage.db import get_async_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/discovery", tags=["admin"])

MemeSort = Literal["per_100", "redrops", "redrops_window", "popularity", "saves", "impressions"]
QuerySort = Literal["times", "last", "min_results"]
MissSort = Literal["times", "explicit", "last"]
Kind = Literal["search", "meme_text", "all"]


def _limit() -> int:
    return Query(50, ge=1, le=200)


async def get_discovery_service(db: AsyncSession = Depends(get_async_db)) -> AsyncGenerator[DiscoveryReportService, None]:
    yield DiscoveryReportService(db)


def _unprocessable(error: ValueError) -> HTTPException:
    # Our own validation messages (option names and values); they never contain search text.
    return HTTPException(status_code=422, detail=str(error))


@router.get("/memes", response_model=DiscoveryMemes)
async def get_memes(
        response: Response,
        windowDays: Optional[int] = Query(None, ge=1), sources: Optional[str] = None,
        minRedrops: int = Query(1, ge=0), maxImpressions: Optional[int] = Query(None, ge=0),
        sort: MemeSort = "per_100", limit: int = _limit(),
        service: DiscoveryReportService = Depends(get_discovery_service)):
    response.headers.update(no_cache_headers())
    if sources is None:
        chosen = DEFAULT_SOURCES
    else:
        chosen = tuple(part.strip() for part in sources.split(",") if part.strip())
        if not chosen:
            raise HTTPException(status_code=422, detail="choose at least one source")
    try:
        return await service.memes(
            window_days=windowDays if windowDays is not None else window_days(), sources=chosen,
            min_redrops=minRedrops, max_impressions=maxImpressions, sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery memes failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None


@router.get("/queries", response_model=DiscoveryQueries)
async def get_queries(
        response: Response,
        kind: Kind = "search", maxResults: int = Query(3, ge=0), includeDegraded: bool = False,
        sinceDays: Optional[int] = Query(None, ge=1), minTimes: int = Query(1, ge=1),
        sort: QuerySort = "times", limit: int = _limit(),
        service: DiscoveryReportService = Depends(get_discovery_service)):
    response.headers.update(no_cache_headers())
    try:
        return await service.queries(
            kind=kind, max_results=maxResults, include_degraded=includeDegraded, since_days=sinceDays,
            min_times=minTimes, sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery queries failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None


@router.get("/misses", response_model=DiscoveryMisses)
async def get_misses(
        response: Response,
        gapMinutes: int = Query(5, ge=1), kind: Kind = "search", sinceDays: Optional[int] = Query(None, ge=1),
        minTimes: int = Query(1, ge=1), explicitOnly: bool = False, sort: MissSort = "times", limit: int = _limit(),
        service: DiscoveryReportService = Depends(get_discovery_service)):
    response.headers.update(no_cache_headers())
    try:
        return await service.misses(
            gap_minutes=gapMinutes, kind=kind, since_days=sinceDays, min_times=minTimes, explicit_only=explicitOnly,
            sort=sort, limit=limit)
    except ValueError as error:
        raise _unprocessable(error) from None
    except Exception as error:
        logger.warning("discovery misses failed: %s", type(error).__name__)
        raise HTTPException(status_code=500, detail="discovery request failed") from None
