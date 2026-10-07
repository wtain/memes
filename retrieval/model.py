"""Plain data types of the retrieval stage (P4 spec, decisions 3 to 5). Pure."""
from dataclasses import dataclass, field
from typing import Mapping

from retrieval.config import AXES

AxisValues = tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class QueryView:
    """What the scorer needs from a query profile: the situation phrase and the values per axis that has any."""
    situation: str | None
    axes: Mapping[str, AxisValues]
    language: str
    degraded: bool

    @classmethod
    def from_profile(cls, profile: Mapping) -> "QueryView":
        axes = {}
        for axis in AXES:
            values = tuple((value["key"], float(value.get("confidence", 1.0))) for value in profile.get(axis) or [])
            if values:
                axes[axis] = values
        situation = (profile.get("situation") or "").strip() or None
        return cls(situation=situation, axes=axes, language=profile.get("language", "other"),
                   degraded=bool(profile.get("degraded")))

    def present_axes(self, *, lexical_active: bool) -> tuple[str, ...]:
        present = []
        if self.situation:
            present.append("situation")
        present.extend(axis for axis in AXES if axis in self.axes)
        if lexical_active:
            present.append("lexical")
        return tuple(present)


@dataclass(frozen=True)
class Filters:
    axes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    tags: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not any(self.axes.values()) and not any(self.tags.values())

    def has_axis_filters(self) -> bool:
        return any(self.axes.values())

    def to_dict(self) -> dict:
        return {"axes": {axis: sorted(keys) for axis, keys in sorted(self.axes.items()) if keys},
                "tags": {key: sorted(values) for key, values in sorted(self.tags.items()) if values}}


@dataclass(frozen=True)
class Candidate:
    image_id: str
    situation_text: str | None
    use_when: str | None
    cosine: float | None                    # similarity of the situation vectors, None without an embedding
    values: Mapping[str, AxisValues]        # axis -> ((key, confidence), ...) of the meme profile
    lexical: float                          # 0..1, 0 when the lexical source did not return it
    axis_path: bool                         # False for a lexical-only image without a profile
    cluster_id: int | None


@dataclass(frozen=True)
class Match:
    query_key: str
    meme_key: str
    similarity: float
    meme_confidence: float


@dataclass(frozen=True)
class AxisExplanation:
    axis: str
    score: float
    weight: float
    contribution: float
    neutral: bool
    query_values: tuple[str, ...]
    matched: tuple[Match, ...]


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: Candidate
    score: float
    axes: tuple[AxisExplanation, ...]
