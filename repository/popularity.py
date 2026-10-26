"""Data access for meme popularity (epic meme-popularity, P1): the absorption tree and its cached counts.
Never commits: the caller controls commit timing."""
import uuid

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from Storage.models import Image, PopularityAbsorption, PopularityKind, PopularitySource

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
    WHERE a.kind = 'absorb' AND a.reversed_by_id IS NULL AND a.loser_image_id IS NOT NULL
""")


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
        await self.session.execute(
            select(Image.id).where(Image.id.in_(sorted(set(ids), key=str))).order_by(Image.id).with_for_update()
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
