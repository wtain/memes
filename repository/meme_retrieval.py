"""SQL side of meme-from-text retrieval (P4 spec, decision 3). Read-only: plain Python values out, no commits.

Eligible set (axis path): active, not flagged, has a meme profile, inside the optional restriction, passing the
hard filters. The lexical source may additionally return images WITHOUT a profile (labelled axis_path False),
never when an axis filter is present."""
import uuid
from collections import defaultdict
from typing import Collection, Mapping, Sequence

from sqlalchemy import and_, case, exists, func, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from repository.ocr_lemmas import lexical_or_scores
from retrieval.candidates import merge_ranked
from retrieval.model import AxisValues, Candidate, Filters
from Storage.models import (
    Image, ImageExtras, ImageTag, MemeProfile, MemeProfileSituationEmbedding as Embedding,
    MemeProfileValue as Value, TmpImageClusters,
)

MAX_EF_SEARCH = 1000


class MemeRetrievalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ----------------------------------------------------------------- eligibility

    @staticmethod
    def _active_unflagged() -> list:
        return [Image.status == "active", or_(ImageExtras.flagged.is_(None), ImageExtras.flagged.is_(False))]

    @staticmethod
    def _tag_condition(tags: Mapping[str, Sequence[str]]):
        pairs = [and_(ImageTag.key == key, ImageTag.value.in_(list(values))) for key, values in tags.items() if values]
        if not pairs:
            return None
        return Image.id.in_(select(ImageTag.image_id).where(or_(*pairs)))

    def _eligible_ids(self, filters: Filters, restrict_ids: Collection[uuid.UUID] | None):
        """Select of image ids that have a profile and pass every condition."""
        statement = (
            select(MemeProfile.image_id)
            .join(Image, Image.id == MemeProfile.image_id)
            .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
            .where(*self._active_unflagged())
        )
        if restrict_ids is not None:
            statement = statement.where(Image.id.in_(list(restrict_ids)))
        tag_condition = self._tag_condition(filters.tags)
        if tag_condition is not None:
            statement = statement.where(tag_condition)
        for axis, keys in filters.axes.items():
            if keys:
                statement = statement.where(exists().where(and_(
                    Value.image_id == MemeProfile.image_id, Value.axis == axis, Value.value_key.in_(list(keys)))))
        return statement

    # ----------------------------------------------------------------- sources

    async def _prepare_vector_search(self, k: int) -> None:
        """pgvector 0.8: let a filtered HNSW scan keep going and return up to k rows. Ignored when unsupported."""
        ef_search = min(max(int(k), 40), MAX_EF_SEARCH)
        for statement in (f"SET LOCAL hnsw.ef_search = {ef_search}", "SET LOCAL hnsw.iterative_scan = relaxed_order"):
            try:
                async with self.session.begin_nested():
                    await self.session.execute(text(statement))
            except DBAPIError:
                pass

    async def _situation_ids(self, vector: list[float], model_name: str, k: int, eligible) -> list[uuid.UUID]:
        await self._prepare_vector_search(k)
        distance = Embedding.embedding.cosine_distance(vector)
        rows = await self.session.execute(
            select(Embedding.image_id)
            .where(Embedding.model_name == model_name, Embedding.image_id.in_(eligible))
            .order_by(distance, Embedding.image_id)
            .limit(k)
        )
        return [row[0] for row in rows.all()]

    async def _axis_ids(self, query_axes: Mapping[str, AxisValues],
                        neighbours: Mapping[str, Mapping[str, Sequence[tuple[str, float]]]] | None,
                        k: int, eligible) -> list[uuid.UUID]:
        weights: dict[tuple[str, str], float] = {}
        for axis, values in query_axes.items():
            for key, confidence in values:
                weights[(axis, key)] = max(weights.get((axis, key), 0.0), confidence)
                for neighbour_key, score in (neighbours or {}).get(axis, {}).get(key, ()):
                    weights[(axis, neighbour_key)] = max(weights.get((axis, neighbour_key), 0.0), confidence * score)
        if not weights:
            return []
        weight_expression = case(
            *[(and_(Value.axis == axis, Value.value_key == key), weight) for (axis, key), weight in weights.items()],
            else_=0.0)
        total = func.sum(Value.confidence * weight_expression)
        rows = await self.session.execute(
            select(Value.image_id)
            .where(or_(*[and_(Value.axis == axis, Value.value_key == key) for (axis, key) in weights]),
                   Value.image_id.in_(eligible))
            .group_by(Value.image_id)
            .order_by(total.desc(), Value.image_id)
            .limit(k)
        )
        return [row[0] for row in rows.all()]

    async def _lexical(self, text_value: str, filters: Filters, restrict_ids, eligible, k: int) -> dict[uuid.UUID, float]:
        scores = await lexical_or_scores(self.session, text_value, restrict_ids)
        if not scores:
            return {}
        if filters.has_axis_filters():
            allowed_rows = await self.session.execute(
                select(MemeProfile.image_id).where(MemeProfile.image_id.in_(list(scores)),
                                                   MemeProfile.image_id.in_(eligible)))
        else:
            statement = (
                select(Image.id)
                .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
                .where(*self._active_unflagged(), Image.id.in_(list(scores)))
            )
            tag_condition = self._tag_condition(filters.tags)
            if tag_condition is not None:
                statement = statement.where(tag_condition)
            allowed_rows = await self.session.execute(statement)
        allowed = {row[0] for row in allowed_rows.all()}
        top = sorted(((image_id, score) for image_id, score in scores.items() if image_id in allowed),
                     key=lambda pair: (-pair[1], str(pair[0])))[:k]
        if not top:
            return {}
        maximum = top[0][1]
        return {image_id: score / maximum for image_id, score in top}

    # ----------------------------------------------------------------- public

    async def generate_candidates(
        self, *, query_vector: list[float] | None, query_axes: Mapping[str, AxisValues],
        neighbours: Mapping[str, Mapping[str, Sequence[tuple[str, float]]]] | None, lexical_text: str | None,
        filters: Filters, restrict_ids: Collection[uuid.UUID] | None, model_name: str, situation_k: int,
        axis_k: int, lexical_k: int, total_cap: int,
    ) -> list[Candidate]:
        eligible = self._eligible_ids(filters, restrict_ids)
        sources: list[list[uuid.UUID]] = []
        if query_vector is not None:
            sources.append(await self._situation_ids(query_vector, model_name, situation_k, eligible))
        if query_axes:
            sources.append(await self._axis_ids(query_axes, neighbours, axis_k, eligible))
        lexical: dict[uuid.UUID, float] = {}
        if lexical_text:
            lexical = await self._lexical(lexical_text, filters, restrict_ids, eligible, lexical_k)
            sources.append(sorted(lexical, key=lambda image_id: (-lexical[image_id], str(image_id))))
        ids = merge_ranked(sources, total_cap)
        if not ids:
            return []
        return await self._load(ids, query_vector, model_name, lexical)

    async def _load(self, ids: list[uuid.UUID], vector: list[float] | None, model_name: str,
                    lexical: Mapping[uuid.UUID, float]) -> list[Candidate]:
        profiles = {
            row.image_id: row for row in (await self.session.execute(
                select(MemeProfile.image_id, MemeProfile.situation_text, MemeProfile.use_when)
                .where(MemeProfile.image_id.in_(ids)))).all()
        }
        values: dict[uuid.UUID, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
        for image_id, axis, key, confidence in (await self.session.execute(
                select(Value.image_id, Value.axis, Value.value_key, Value.confidence)
                .where(Value.image_id.in_(ids))
                .order_by(Value.image_id, Value.axis, Value.confidence.desc(), Value.value_key))).all():
            values[image_id][axis].append((key, float(confidence)))
        cosines: dict[uuid.UUID, float] = {}
        if vector is not None:
            distance = Embedding.embedding.cosine_distance(vector)
            for image_id, value in (await self.session.execute(
                    select(Embedding.image_id, distance)
                    .where(Embedding.image_id.in_(ids), Embedding.model_name == model_name))).all():
                cosines[image_id] = 1.0 - float(value)
        clusters = {
            image_id: cluster_id for image_id, cluster_id in (await self.session.execute(
                select(TmpImageClusters.image_id, TmpImageClusters.cluster_id)
                .where(TmpImageClusters.image_id.in_(ids)))).all()
        }
        candidates = []
        for image_id in ids:
            profile = profiles.get(image_id)
            candidates.append(Candidate(
                image_id=str(image_id),
                situation_text=profile.situation_text if profile else None,
                use_when=profile.use_when if profile else None,
                cosine=cosines.get(image_id),
                values={axis: tuple(pairs) for axis, pairs in values.get(image_id, {}).items()},
                lexical=float(lexical.get(image_id, 0.0)),
                axis_path=profile is not None,
                cluster_id=clusters.get(image_id),
            ))
        return candidates

    async def load_basics(self, image_ids: Collection[str]) -> dict[str, tuple[str, bool]]:
        """Filename and flagged state for hydrating result items."""
        ids = [uuid.UUID(image_id) for image_id in image_ids]
        if not ids:
            return {}
        rows = await self.session.execute(
            select(Image.id, Image.filename, ImageExtras.flagged)
            .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
            .where(Image.id.in_(ids)))
        return {str(image_id): (filename, bool(flagged)) for image_id, filename, flagged in rows.all()}
