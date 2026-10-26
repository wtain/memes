"""Data access for meme popularity (epic meme-popularity, P1): the absorption tree and its cached counts.
Never commits: the caller controls commit timing."""
import uuid
from collections import defaultdict
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Image, PopularityAbsorption, PopularityKind, PopularityReason, PopularitySource

MAX_HOPS = 10000
MAX_ATTEMPTS = 3

_TREE_WEIGHT_SQL = text("""
    WITH RECURSIVE tree(id) AS (
        SELECT CAST(:root AS uuid)
        UNION
        SELECT a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE a.kind = 'absorb' AND a.reversed_by_id IS NULL AND a.loser_image_id IS NOT NULL
    )
    SELECT COALESCE(SUM(a.weight), 0) FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
    WHERE a.kind = 'absorb' AND a.reversed_by_id IS NULL
""")

_BREAKDOWN_SQL = text("""
    WITH RECURSIVE tree(id) AS (
        SELECT CAST(:root AS uuid)
        UNION
        SELECT a.loser_image_id FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
        WHERE a.kind = 'absorb' AND a.reversed_by_id IS NULL AND a.loser_image_id IS NOT NULL
    )
    SELECT a.source, SUM(a.weight) FROM popularity_absorptions a JOIN tree t ON a.winner_image_id = t.id
    WHERE a.kind = 'absorb' AND a.reversed_by_id IS NULL
    GROUP BY a.source
""")


@dataclass(frozen=True)
class Drift:
    image_id: uuid.UUID
    cached: int
    expected: int


@dataclass(frozen=True)
class DroppedCopy:
    filename: str
    content_hash: str
    destination: str


class CycleError(ValueError):
    """The winner's tree already contains the loser."""


class AlreadyDiscardedError(Exception):
    """The loser already has an open edge into a different tree."""

    def __init__(self, existing: PopularityAbsorption):
        super().__init__(f"image {existing.loser_image_id} is already absorbed into {existing.winner_image_id}")
        self.existing = existing


class PopularityConflictError(RuntimeError):
    """The root kept changing under concurrent writers."""


class PopularityRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    # ---- tree queries ----

    async def _open_edge(self, loser_id: uuid.UUID) -> PopularityAbsorption | None:
        return (await self.session.execute(
            select(PopularityAbsorption).where(
                PopularityAbsorption.loser_image_id == loser_id,
                PopularityAbsorption.kind == PopularityKind.ABSORB,
                PopularityAbsorption.reversed_by_id.is_(None),
            )
        )).scalar_one_or_none()

    async def root_of(self, image_id: uuid.UUID) -> uuid.UUID:
        """Follow open absorb edges upward; the image itself when it has none."""
        current = image_id
        for _ in range(MAX_HOPS):
            edge = await self._open_edge(current)
            if edge is None:
                return current
            current = edge.winner_image_id
        raise RuntimeError(f"absorption chain above {image_id} exceeds {MAX_HOPS} hops")

    async def tree_weight(self, image_id: uuid.UUID) -> int:
        """Sum of weights of the open edges below `image_id`; tree size is 1 + this."""
        result = await self.session.execute(_TREE_WEIGHT_SQL, {"root": image_id})
        return int(result.scalar_one())

    # ---- shared helpers for the write operations ----

    async def _lock_images(self, ids) -> None:
        """Row-lock images in id order (consistent order avoids deadlocks); missing ids are ignored."""
        # key_share=True renders FOR NO KEY UPDATE: plain FOR UPDATE conflicts with the FOR KEY SHARE lock
        # Postgres takes on every FK check, so it would block inserts into all child tables (ocr_texts, tags...).
        await self.session.execute(
            select(Image.id).where(Image.id.in_(set(ids))).order_by(Image.id).with_for_update(key_share=True)
        )

    async def _add_popularity(self, image_id: uuid.UUID, delta: int) -> None:
        await self.session.execute(
            update(Image).where(Image.id == image_id).values(popularity=Image.popularity + delta)
        )

    async def _zero_popularity(self, image_id: uuid.UUID) -> None:
        await self.session.execute(update(Image).where(Image.id == image_id).values(popularity=0))

    async def _image(self, image_id: uuid.UUID) -> Image | None:
        return (await self.session.execute(select(Image).where(Image.id == image_id))).scalar_one_or_none()

    # ---- operations ----

    async def absorb(self, loser_id: uuid.UUID, winner_id: uuid.UUID, source: str,
                     batch_run_id: uuid.UUID | None = None) -> PopularityAbsorption:
        """Record `loser_id` as a copy of `winner_id` (credited to the root of the winner's tree) and move
        the loser's tree count into that root. Idempotent for a retry into the same tree."""
        if source not in PopularitySource.ALL:
            raise ValueError(f"unknown source {source!r}")
        # Locks from a failed attempt stay held, so id ordering is not global across retries; a deadlock
        # there is detected and aborted by Postgres, never silently wrong.
        for _ in range(MAX_ATTEMPTS):
            root = await self.root_of(winner_id)
            if root == loser_id:
                raise CycleError(f"{winner_id} is already inside the tree of {loser_id}")
            await self._lock_images({loser_id, root})
            if await self.root_of(winner_id) != root:
                continue

            loser = await self._image(loser_id)
            if loser is None:
                raise ValueError(f"image {loser_id} does not exist")

            existing = await self._open_edge(loser_id)
            if existing is not None:
                if await self.root_of(existing.winner_image_id) == root:
                    return existing
                raise AlreadyDiscardedError(existing)

            winner = await self._image(winner_id)
            weight = 0 if source == PopularitySource.RECUT else 1
            row = PopularityAbsorption(
                kind=PopularityKind.ABSORB,
                source=source,
                loser_image_id=loser_id,
                winner_image_id=winner_id,
                weight=weight,
                loser_filename=loser.filename,
                loser_content_hash=loser.content_hash,
                winner_content_hash=winner.content_hash if winner is not None else None,
                batch_run_id=batch_run_id,
            )
            self.session.add(row)
            await self.session.flush()

            delta = weight + await self.tree_weight(loser_id)
            await self._add_popularity(root, delta)
            await self._zero_popularity(loser_id)
            await self.session.flush()
            return row
        raise PopularityConflictError(f"root above {winner_id} kept changing")

    async def record_dropped_copies(self, winner_id: uuid.UUID, copies: Sequence[DroppedCopy],
                                    batch_run_id: uuid.UUID,
                                    source: str = PopularitySource.HASH_IN_BATCH) -> int:
        """Credit the root of `winner_id`'s tree with files dropped as exact duplicates (leaf edges, no loser
        image). Idempotent per (batch, content hash, destination); returns the number newly counted."""
        if batch_run_id is None:
            raise ValueError("batch_run_id is required")
        if source not in (PopularitySource.HASH_IN_BATCH, PopularitySource.HASH_CROSS_CORPUS):
            raise ValueError(f"source {source!r} is not valid for dropped copies")
        if not copies:
            return 0
        for _ in range(MAX_ATTEMPTS):
            root = await self.root_of(winner_id)
            await self._lock_images({root})
            if await self.root_of(winner_id) != root:
                continue
            winner = await self._image(winner_id)
            winner_hash = winner.content_hash if winner is not None else None
            inserted = 0
            for copy in copies:
                stmt = (
                    pg_insert(PopularityAbsorption)
                    .values(
                        kind=PopularityKind.ABSORB, source=source, loser_image_id=None,
                        winner_image_id=winner_id, weight=1, loser_filename=copy.filename,
                        loser_content_hash=copy.content_hash, winner_content_hash=winner_hash,
                        dedupe_key=f"{batch_run_id}:{copy.content_hash}:{copy.destination}",
                        batch_run_id=batch_run_id,
                    )
                    .on_conflict_do_nothing(index_elements=["dedupe_key"])
                    .returning(PopularityAbsorption.id)
                )
                if (await self.session.execute(stmt)).first() is not None:
                    inserted += 1
            if inserted:
                await self._add_popularity(root, inserted)
            await self.session.flush()
            return inserted
        raise PopularityConflictError(f"root above {winner_id} kept changing")

    async def reverse(self, absorb_id: uuid.UUID, reason: str) -> PopularityAbsorption:
        """Undo one open absorb edge: the loser (with its subtree) leaves the winner's tree again."""
        if reason not in PopularityReason.ALL:
            raise ValueError(f"unknown reason {reason!r}")
        for _ in range(MAX_ATTEMPTS):
            edge = (await self.session.execute(
                select(PopularityAbsorption).where(PopularityAbsorption.id == absorb_id)
            )).scalar_one_or_none()
            if edge is None or edge.kind != PopularityKind.ABSORB or edge.reversed_by_id is not None:
                raise ValueError(f"{absorb_id} is not an open absorb row")
            root = await self.root_of(edge.winner_image_id)
            # A ghost root has no images row and cannot be locked; its UPDATE below touches zero rows.
            await self._lock_images({root} | ({edge.loser_image_id} if edge.loser_image_id else set()))
            if await self.root_of(edge.winner_image_id) != root:
                continue
            await self.session.refresh(edge)
            if edge.reversed_by_id is not None:
                raise ValueError(f"{absorb_id} is not an open absorb row")

            subtree = await self.tree_weight(edge.loser_image_id) if edge.loser_image_id else 0
            row = PopularityAbsorption(
                kind=PopularityKind.REVERSE, source=edge.source, loser_image_id=edge.loser_image_id,
                winner_image_id=edge.winner_image_id, weight=edge.weight,
                loser_filename=edge.loser_filename, loser_content_hash=edge.loser_content_hash,
                winner_content_hash=edge.winner_content_hash, batch_run_id=edge.batch_run_id,
                reverses_id=edge.id, reason=reason,
            )
            self.session.add(row)
            await self.session.flush()
            edge.reversed_by_id = row.id
            await self.session.flush()

            await self._add_popularity(root, -(edge.weight + subtree))
            if edge.loser_image_id is not None:
                await self.session.execute(
                    update(Image).where(Image.id == edge.loser_image_id).values(popularity=1 + subtree)
                )
            await self.session.flush()
            return row
        raise PopularityConflictError(f"root above edge {absorb_id} kept changing")

    # ---- read-only checks ----

    async def popularity_of(self, image_id: uuid.UUID) -> int:
        """Popularity derived from the edges: 0 for a discarded image, else 1 + the weight of its tree."""
        if await self._open_edge(image_id) is not None:
            return 0
        return 1 + await self.tree_weight(image_id)

    async def breakdown(self, image_id: uuid.UUID) -> dict[str, int]:
        """`own` plus the weight per source over the open edges below the image (sources with weight 0,
        such as recut, add nothing and are left out). Meant for roots: on a discarded image `popularity_of` is 0
        while this still returns the tree-based parts, so callers should call it on roots only."""
        result = await self.session.execute(_BREAKDOWN_SQL, {"root": image_id})
        parts = {"own": 1}
        for source, weight in result.all():
            if weight:
                parts[source] = int(weight)
        return parts

    async def verify(self, image_ids: Collection[uuid.UUID] | None = None) -> list[Drift]:
        """Compare images.popularity with the value derived from all open edges (loaded once). Only existing
        images are compared, so trees whose root image is gone are ignored."""
        edges = (await self.session.execute(
            select(PopularityAbsorption.winner_image_id, PopularityAbsorption.loser_image_id,
                   PopularityAbsorption.weight).where(
                PopularityAbsorption.kind == PopularityKind.ABSORB,
                PopularityAbsorption.reversed_by_id.is_(None),
            )
        )).all()
        children: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
        own_weight: dict[uuid.UUID, int] = defaultdict(int)
        losers: set[uuid.UUID] = set()
        for winner, loser, weight in edges:
            own_weight[winner] += weight
            if loser is not None:
                children[winner].append(loser)
                losers.add(loser)

        query = select(Image.id, Image.popularity)
        if image_ids is not None:
            query = query.where(Image.id.in_(set(image_ids)))
        drifts = []
        for image_id, cached in (await self.session.execute(query)).all():
            if image_id in losers:
                expected = 0
            else:
                expected, seen, stack = 1, {image_id}, [image_id]
                while stack:
                    node = stack.pop()
                    expected += own_weight.get(node, 0)
                    for child in children.get(node, ()):
                        if child not in seen:
                            seen.add(child)
                            stack.append(child)
            if cached != expected:
                drifts.append(Drift(image_id, cached, expected))
        return drifts
