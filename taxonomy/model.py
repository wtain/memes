from dataclasses import dataclass, field
from typing import Mapping

FREE_TEXT = "free_text"
VOCABULARY = "vocabulary"
LANGUAGES = ("en", "ru", "es")


@dataclass(frozen=True)
class AxisSpec:
    name: str
    type: str
    max_chars: int | None = None
    max_values: int | None = None


@dataclass(frozen=True)
class VocabValue:
    key: str
    definition: str
    labels: Mapping[str, str]


@dataclass(frozen=True)
class Taxonomy:
    version: int
    axes: Mapping[str, AxisSpec]
    values: Mapping[str, Mapping[str, VocabValue]]
    content_hash: str
    similarity_overrides: Mapping[str, Mapping] = field(default_factory=dict)
    overrides_hash: str = ""

    def controlled_axes(self) -> tuple[str, ...]:
        return tuple(name for name, spec in self.axes.items() if spec.type == VOCABULARY)

    def has_key(self, axis: str, key: str) -> bool:
        return key in self.values.get(axis, {})
