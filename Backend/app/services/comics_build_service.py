"""Build a draft comics into one active image (epic multi-image-comics, C4;
docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-05-c4-build-design.md, "The build, step by step").

One build is one transaction owned by the caller (get_async_db commits; this service never commits). The comics row
lock is taken first, so two builds of one comics serialise and the second sees `built`. Every check that needs no file
is done before composing. The composed file is written under a unique hidden temporary name, hashed, and only then
moved onto its final name `comics-<hash12>.<ext>`; from that moment a rollback removes it (recut_files.file_effects),
and the file of an existing image is never touched because a final name that an images row already holds is refused
(`same_as_existing`) before the move. That check runs under a transaction-scoped advisory lock on the content hash, so two
builds of different comics with byte-identical output serialise there and the second is refused (C5, D12(b)). Source files are only read.

Errors are ComicsError (positions are 1-based): built, not_found, keep_invalid, not_buildable (reason too_few_panels,
panel_missing, image_not_active, image_already_built, same_as_existing with image_id, panel_unavailable with position
and cause not_active | flagged | live_cut | file_missing | file_changed | already_discarded), popularity_conflict, and
compose_failed (reason, position)."""
import asyncio
import os
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from Backend.app.repositories.image_repository import ImageRepository
from Backend.app.services.recut_files import file_effects, remove_quietly
from batch.utils.comics_compose import ComposedFile, ComposeError, compose_to_file, output_format
from batch.utils.file_hash import sha256_file
from config.settings import settings
from repository.comics import MIN_BUILD_PANELS, ComicsError, ComicsRepository, _flagged_ids, _live_cut_ids, _statuses
from repository.popularity import (AlreadyDiscardedError, CycleError, PopularityConflictError, PopularityRepository,
                                   WinnerGoneError)
from Storage.models import Comics, ComicsState, Image, ImageStatus, PopularitySource

COMICS_SOURCE_REASON = "comics_source"
TEMP_PREFIX = ".comics-build-"
_EXTENSIONS = {"JPEG": "jpg", "PNG": "png"}
# Process-wide: one build composes at a time (rare and heavy: every panel decoded at full size).
_BUILD_SEMAPHORE = asyncio.Semaphore(1)


def _unavailable(position: int, cause: str) -> ComicsError:
    return ComicsError("not_buildable", reason="panel_unavailable", position=position, cause=cause)


def _file_cause(path: str, expected_hash: str | None) -> str | None:
    """file_missing when the file cannot be read, file_changed when its sha256 is not the stored content_hash
    (a missing content_hash cannot be verified and counts as changed)."""
    try:
        actual = sha256_file(path)
    except OSError:
        return "file_missing"
    return None if expected_hash is not None and actual == expected_hash else "file_changed"


def _compose_to_temp(paths: list[str], base: str, jpeg_quality: int) -> tuple[str, ComposedFile]:
    """Compose into a unique hidden name in `base`; on any failure that name does not stay behind."""
    tmp = os.path.join(base, f"{TEMP_PREFIX}{uuid.uuid4()}.{_EXTENSIONS[output_format(paths)]}")
    try:
        return tmp, compose_to_file(paths, tmp, jpeg_quality)
    except BaseException:
        remove_quietly(tmp)
        raise


class ComicsBuildService:

    def __init__(self, session: AsyncSession, base_path: str | None = None, jpeg_quality: int | None = None):
        self.session = session
        self.repo = ComicsRepository(session)
        self.images = ImageRepository(session)
        self.popularity = PopularityRepository(session)
        self.base_path = base_path
        self.jpeg_quality = jpeg_quality

    async def build(self, comics_id: uuid.UUID, keep_panel_ids: list[uuid.UUID]) -> tuple[Comics, uuid.UUID]:
        base = self.base_path or settings.get("BASE_PATH")
        quality = self.jpeg_quality or settings.COMICS.JPEG_QUALITY

        # 1. The row lock first; then the refusals that need nothing but the comics.
        comics = await self.repo.get(comics_id, lock=True)
        if comics.state != ComicsState.DRAFT:
            raise ComicsError("built")
        panels = sorted(comics.panels, key=lambda p: p.position)
        keep = set(keep_panel_ids)
        if not keep <= {p.id for p in panels}:
            raise ComicsError("keep_invalid")

        # 2. Validate and re-check every panel (the checks made when it was added can be stale).
        if len(panels) < MIN_BUILD_PANELS:
            raise ComicsError("not_buildable", reason="too_few_panels")
        if any(p.image_id is None for p in panels):
            raise ComicsError("not_buildable", reason="panel_missing")
        image_ids = [p.image_id for p in panels]
        paths = await self._recheck(base, image_ids)

        # 3. Compose under a temporary name, then move it onto the final name.
        built_file, final_name = await self._write_file(base, paths, quality)

        # 4. The images row (no metrics event, no processing-status row: the incremental jobs pick it up).
        built = Image(filename=final_name, content_hash=built_file.content_hash, width=built_file.width,
                      height=built_file.height, status=ImageStatus.ACTIVE)
        self.session.add(built)
        await self.session.flush()
        built_id = built.id

        # 5. Before any panel is released: mark_built re-checks that every panel still has an image.
        await self.repo.mark_built(comics_id, built_id, built_file.content_hash)

        # 6. Retire the unkept panels: flag for the move, then credit their popularity to the built image.
        for position, panel in enumerate(panels, start=1):
            if panel.id in keep:
                continue
            await self.images.set_flagged(panel.image_id, True, COMICS_SOURCE_REASON)
            try:
                await self.popularity.absorb(panel.image_id, built_id, PopularitySource.COMICS_BUILD)
            except AlreadyDiscardedError as exc:
                raise _unavailable(position, "already_discarded") from exc
            except (WinnerGoneError, CycleError, PopularityConflictError) as exc:
                raise ComicsError("popularity_conflict") from exc

        # 7. Release the kept panels (kept = true, image_id = NULL).
        await self.repo.release_panels([p.id for p in panels if p.id in keep])

        # 8. The built comics, freshly loaded.
        return await self.repo.get(comics_id), built_id

    async def _recheck(self, base: str, image_ids: list[uuid.UUID]) -> list[str]:
        """The panels' file paths, in order; the first panel that fails a check is refused by its 1-based position,
        with the causes in the order not_active, flagged, live_cut, file_missing, file_changed."""
        statuses = await _statuses(self.session, image_ids)
        flagged = await _flagged_ids(self.session, image_ids)
        live = await _live_cut_ids(self.session, image_ids)
        rows = await self.session.execute(
            select(Image.id, Image.filename, Image.content_hash).where(Image.id.in_(image_ids)))
        files = {row.id: (row.filename, row.content_hash) for row in rows}
        paths = []
        for position, image_id in enumerate(image_ids, start=1):
            if statuses.get(image_id) != ImageStatus.ACTIVE:
                raise _unavailable(position, "not_active")
            if image_id in flagged:
                raise _unavailable(position, "flagged")
            if image_id in live:
                raise _unavailable(position, "live_cut")
            filename, content_hash = files[image_id]
            path = os.path.join(base, filename)
            cause = await asyncio.to_thread(_file_cause, path, content_hash)
            if cause is not None:
                raise _unavailable(position, cause)
            paths.append(path)
        return paths

    async def _write_file(self, base: str, paths: list[str], quality: int) -> tuple[ComposedFile, str]:
        """Compose, refuse same_as_existing, then os.replace onto comics-<hash12>.<ext> and register its removal on
        rollback. Only the temporary file is ever deleted here."""
        try:
            async with _BUILD_SEMAPHORE:
                tmp, composed = await asyncio.to_thread(_compose_to_temp, paths, base, quality)
        except ComposeError as error:
            position = error.position + 1 if error.position is not None else None
            raise ComicsError("compose_failed", reason=error.reason, position=position) from error
        try:
            final_name = f"comics-{composed.content_hash[:12]}.{_EXTENSIONS[composed.format]}"
            # Two builds of different comics with byte-identical output would otherwise both pass the check below
            # (neither sees the other's uncommitted row). Held until this transaction ends, so the second waits for the
            # first's commit or rollback and then sees its row, or none (D12(b)).
            await self.session.execute(select(func.pg_advisory_xact_lock(func.hashtext(composed.content_hash))))
            existing = (await self.session.execute(
                select(Image.id).where(or_(Image.content_hash == composed.content_hash, Image.filename == final_name))
                .order_by(Image.created_at, Image.id).limit(1))).scalar_one_or_none()
            if existing is not None:
                raise ComicsError("not_buildable", reason="same_as_existing", image_id=existing)
            final = os.path.join(base, final_name)
            # A file of that name with no row is the orphan of an earlier crashed build with identical content.
            os.replace(tmp, final)
            # Registered the moment the final file exists and before any SQL, so no later failure can orphan it.
            file_effects(self.session, on_rollback=lambda: remove_quietly(final))
            return composed, final_name
        finally:
            remove_quietly(tmp)  # a no-op once the replace has moved it
