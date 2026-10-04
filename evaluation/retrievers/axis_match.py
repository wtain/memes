import uuid
from collections import defaultdict
from typing import Sequence

from sqlalchemy import select

from evaluation.gold import GoldQuery
from Storage.models import MemeProfileValue


class AxisMatchRetriever:
    """Baseline using the query's GOLD expected profile (no parsing): per axis with expected values, the
    confidence-weighted share of expected values present on the image's profile; the score is the mean over
    those axes. Isolates the quality of P2's profiles from P3's parsing. Not the P4 fusion."""

    name = "axis_match"
    config: dict = {}

    def __init__(self, session):
        self.session = session

    async def retrieve(self, query: GoldQuery, corpus_ids: Sequence[str], k: int) -> list[tuple[str, float]]:
        expected = {axis: values for axis, values in query.expected.axis_values().items() if values}
        if not expected:
            return []
        rows = await self.session.execute(
            select(MemeProfileValue.image_id, MemeProfileValue.axis, MemeProfileValue.value_key,
                   MemeProfileValue.confidence)
            .where(MemeProfileValue.image_id.in_([uuid.UUID(image_id) for image_id in corpus_ids]),
                   MemeProfileValue.axis.in_(list(expected)))
        )
        by_image: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
        for image_id, axis, key, confidence in rows.all():
            by_image[str(image_id)][axis][key] = confidence
        scored = []
        for image_id, axes in by_image.items():
            per_axis = [sum(axes.get(axis, {}).get(key, 0.0) for key in values) / len(values)
                        for axis, values in expected.items()]
            score = sum(per_axis) / len(per_axis)
            if score > 0:
                scored.append((image_id, score))
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k]
