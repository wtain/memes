"""SQL side of meme-from-text retrieval (P4 spec, decision 3). Read-only: plain Python values out, no commits.

Eligible set (axis path): active, not flagged, has a meme profile, inside the optional restriction, passing the
hard filters. The lexical source may additionally return images WITHOUT a profile (labelled axis_path False),
never when an axis filter is present."""
import uuid
from collections import defaultdict
from typing import Collection, Mapping, Sequence

from pgvector.sqlalchemy import Vector
from sqlalchemy import Float, String, and_, any_, bindparam, case, cast, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, REAL, UUID as PG_UUID
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from repository.ocr_lemmas import lexical_or_scores
from retrieval.candidates import merge_ranked
from retrieval.model import AxisValues, Candidate, Filters
from Storage.models import (
    PROFILE_EMBEDDING_DIM, Image, ImageExtras, ImageTag, MemeProfile, MemeProfileSituationEmbedding as Embedding,
    MemeProfileValue as Value, TmpImageClusters,
)

MAX_EF_SEARCH = 1000
ITERATIVE_SCAN_SETTING = "hnsw.iterative_scan"
# None = not probed yet in this process; True/False = the server accepts / rejects hnsw.iterative_scan.
_iterative_scan_supported: bool | None = None


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector text form with %.9g per element: lossless for float32 and about half the size of repr()."""
    return "[" + ",".join("%.9g" % value for value in vector) + "]"


def _in_ids(column, ids: Collection[uuid.UUID]):
    """`column = ANY(:ids)` with one array parameter instead of an expanded IN list."""
    return column == any_(bindparam("ids", list(ids), type_=ARRAY(PG_UUID(as_uuid=True)), unique=True))


def _cosine_distance(vector: Sequence[float], *, binary: bool = False):
    """Embedding <=> query. By default the query vector is one compact text literal cast to vector (~14 KB).
    With binary=True it travels as a float4 array (4 KB, same float32 values): used by the statement that also
    carries an id array, which would otherwise pass the 16,384-byte request size (see the module notes)."""
    if binary:
        query = cast(cast(bindparam("query_vector", [float(value) for value in vector], type_=ARRAY(REAL)),
                          ARRAY(REAL)), Vector(PROFILE_EMBEDDING_DIM))
    else:
        query = cast(bindparam("query_vector", _vector_literal(vector), type_=String), Vector(PROFILE_EMBEDDING_DIM))
    return Embedding.embedding.op("<=>", return_type=Float)(query)


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
            statement = statement.where(_in_ids(Image.id, restrict_ids))
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
        """pgvector 0.8: let a filtered HNSW scan keep going and return up to k rows, in one round trip.

        The first call of the process probes hnsw.iterative_scan inside a savepoint (an unsupported setting fails
        the statement); later calls reuse the answer and need no savepoint."""
        global _iterative_scan_supported
        ef_search = str(min(max(int(k), 40), MAX_EF_SEARCH))
        both = text(f"SELECT set_config('hnsw.ef_search', :ef, true), set_config('{ITERATIVE_SCAN_SETTING}', "
                    f"'relaxed_order', true)")
        only_ef = text("SELECT set_config('hnsw.ef_search', :ef, true)")
        if _iterative_scan_supported:
            await self.session.execute(both, {"ef": ef_search})
            return
        if _iterative_scan_supported is None:
            try:
                async with self.session.begin_nested():
                    await self.session.execute(both, {"ef": ef_search})
                _iterative_scan_supported = True
                return
            except DBAPIError:
                _iterative_scan_supported = False
        try:
            async with self.session.begin_nested():
                await self.session.execute(only_ef, {"ef": ef_search})
        except DBAPIError:
            pass

    async def _situation_ids(self, vector: list[float], model_name: str, k: int,
                             eligible) -> dict[uuid.UUID, float]:
        """Nearest eligible ids in rank order, mapped to their cosine similarity (1 - cosine distance)."""
        await self._prepare_vector_search(k)
        distance = _cosine_distance(vector)
        rows = await self.session.execute(
            select(Embedding.image_id, distance)
            .where(Embedding.model_name == model_name, Embedding.image_id.in_(eligible))
            .order_by(distance, Embedding.image_id)
            .limit(k)
        )
        return {image_id: 1.0 - float(value) for image_id, value in rows.all()}

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
                select(MemeProfile.image_id).where(_in_ids(MemeProfile.image_id, scores),
                                                   MemeProfile.image_id.in_(eligible)))
        else:
            statement = (
                select(Image.id)
                .outerjoin(ImageExtras, ImageExtras.image_id == Image.id)
                .where(*self._active_unflagged(), _in_ids(Image.id, scores))
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
        situation_cosines: dict[uuid.UUID, float] = {}
        if query_vector is not None:
            situation_cosines = await self._situation_ids(query_vector, model_name, situation_k, eligible)
            sources.append(list(situation_cosines))
        if query_axes:
            sources.append(await self._axis_ids(query_axes, neighbours, axis_k, eligible))
        lexical: dict[uuid.UUID, float] = {}
        if lexical_text:
            lexical = await self._lexical(lexical_text, filters, restrict_ids, eligible, lexical_k)
            sources.append(sorted(lexical, key=lambda image_id: (-lexical[image_id], str(image_id))))
        ids = merge_ranked(sources, total_cap)
        if not ids:
            return []
        return await self._load(ids, query_vector, model_name, lexical, situation_cosines)

    async def _load(self, ids: list[uuid.UUID], vector: list[float] | None, model_name: str,
                    lexical: Mapping[uuid.UUID, float],
                    known_cosines: Mapping[uuid.UUID, float] | None = None) -> list[Candidate]:
        profiles = {
            row.image_id: row for row in (await self.session.execute(
                select(MemeProfile.image_id, MemeProfile.situation_text, MemeProfile.use_when)
                .where(_in_ids(MemeProfile.image_id, ids)))).all()
        }
        values: dict[uuid.UUID, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
        for image_id, axis, key, confidence in (await self.session.execute(
                select(Value.image_id, Value.axis, Value.value_key, Value.confidence)
                .where(_in_ids(Value.image_id, ids))
                .order_by(Value.image_id, Value.axis, Value.confidence.desc(), Value.value_key))).all():
            values[image_id][axis].append((key, float(confidence)))
        cosines: dict[uuid.UUID, float] = {}
        if vector is not None:
            wanted = set(ids)
            cosines.update({image_id: value for image_id, value in (known_cosines or {}).items() if image_id in wanted})
            missing = [image_id for image_id in ids if image_id not in cosines]
            if missing:
                distance = _cosine_distance(vector, binary=True)
                for image_id, value in (await self.session.execute(
                        select(Embedding.image_id, distance)
                        .where(_in_ids(Embedding.image_id, missing), Embedding.model_name == model_name))).all():
                    cosines[image_id] = 1.0 - float(value)
        clusters = {
            image_id: cluster_id for image_id, cluster_id in (await self.session.execute(
                select(TmpImageClusters.image_id, TmpImageClusters.cluster_id)
                .where(_in_ids(TmpImageClusters.image_id, ids)))).all()
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
            .where(_in_ids(Image.id, ids)))
        return {str(image_id): (filename, bool(flagged)) for image_id, filename, flagged in rows.all()}
