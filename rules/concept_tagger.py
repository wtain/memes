from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pymorphy3
import yaml

from rules.normalize import lemmatize_word, make_morph, normalize

logger = logging.getLogger(__name__)


@dataclass
class _Concept:
    name: str
    words: frozenset[str]               # lemmatized; multi-word stored as space-joined "a b"
    fuzzy: list[dict]                   # [{"lemma": str, "threshold": int}, ...]
    votes: dict[tuple[str, str], float] # {(tag_key, tag_value): weight}


@dataclass
class TagResult:
    tags: list[tuple[str, str]]
    trace: list[dict]


class ConceptTagger:
    def __init__(
        self,
        concepts: list[_Concept],
        thresholds: dict[tuple[str, str], float],
        morph: pymorphy3.MorphAnalyzer,
    ):
        self._concepts = concepts
        self._thresholds = thresholds
        self._morph = morph

    @classmethod
    def load(cls, data_dir: str | Path, profile: str) -> ConceptTagger:
        data_dir = Path(data_dir)
        morph = make_morph()
        thresholds, declared = _load_tags(data_dir / f"tags.{profile}.yaml")
        concepts = _load_concepts(data_dir / f"concepts.{profile}.yaml", profile, declared, morph)
        return cls(concepts, thresholds, morph)

    def tag(self, text: str) -> TagResult:
        lemma_bag = normalize(text, self._morph)
        # Phrases may contain short words ("zz" in "zz top", "in" in "alice in chains").
        # normalize() drops them, making phrase checks impossible. Build a separate full
        # bag with no length filter just for phrase matching.
        lemma_bag_full = normalize(text, self._morph, min_length=1)
        scores: dict[tuple[str, str], float] = {}
        trace: list[dict] = []

        for concept in self._concepts:
            reason = _match_concept(concept, lemma_bag, lemma_bag_full)
            if reason is None:
                continue
            for tag, weight in concept.votes.items():
                scores[tag] = scores.get(tag, 0.0) + weight
            trace.append({
                "concept": concept.name,
                "match": reason,
                "votes": {f"{k}:{v}": w for (k, v), w in concept.votes.items()},
            })

        tags = [tag for tag, s in scores.items() if s >= self._thresholds.get(tag, 1.0)]
        return TagResult(tags=sorted(tags), trace=trace)

    def vocabulary_lemmas(self) -> set[str]:
        """All individual lemmas covered by the vocabulary (for coverage metrics)."""
        result: set[str] = set()
        for c in self._concepts:
            for w in c.words:
                result.update(w.split())
            for fe in c.fuzzy:
                result.add(fe["lemma"])
        return result


def _load_tags(
    path: Path,
) -> tuple[dict[tuple[str, str], float], set[tuple[str, str]]]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    default_threshold: float = (data.get("defaults") or {}).get("threshold", 1.0)
    thresholds: dict[tuple[str, str], float] = {}
    declared: set[tuple[str, str]] = set()

    for full_tag, cfg in (data.get("tags") or {}).items():
        key, _, value = full_tag.partition(":")
        tag = (key.strip(), value.strip())
        declared.add(tag)
        override = (cfg or {}).get("threshold") if cfg else None
        thresholds[tag] = override if override is not None else default_threshold

    return thresholds, declared


def _load_concepts(
    path: Path,
    profile: str,
    declared_tags: set[tuple[str, str]],
    morph: pymorphy3.MorphAnalyzer,
) -> list[_Concept]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    word_index: dict[str, list[str]] = {}
    concepts: list[_Concept] = []

    for concept_name, cfg in (data or {}).items():
        raw_words: list[str] = cfg.get("words") or []
        raw_fuzzy: list[dict] = cfg.get("fuzzy") or []
        votes_raw: dict[str, float] = cfg.get("votes") or {}

        for full_tag in votes_raw:
            key, _, value = full_tag.partition(":")
            tag = (key.strip(), value.strip())
            if tag not in declared_tags:
                raise ValueError(
                    f"Concept '{concept_name}' votes for undeclared tag '{full_tag}' "
                    f"(not in tags.{profile}.yaml)"
                )

        # Lemmatize every word entry; multi-word phrases become space-joined lemma strings
        lemmatized: set[str] = set()
        for w in raw_words:
            parts = w.split()
            lemmatized.add(" ".join(lemmatize_word(p, morph) for p in parts))

        fuzzy = [
            {"lemma": lemmatize_word(fe["word"], morph), "threshold": fe["threshold"]}
            for fe in raw_fuzzy
        ]

        votes: dict[tuple[str, str], float] = {}
        for full_tag, weight in votes_raw.items():
            key, _, value = full_tag.partition(":")
            votes[(key.strip(), value.strip())] = float(weight)

        entry = _Concept(
            name=concept_name,
            words=frozenset(lemmatized),
            fuzzy=fuzzy,
            votes=votes,
        )
        concepts.append(entry)

        for w in lemmatized:
            word_index.setdefault(w, []).append(concept_name)

    for word, cnames in word_index.items():
        if len(cnames) > 1:
            logger.warning("Homonym: word '%s' appears in concepts %s", word, cnames)

    return concepts


def _match_concept(concept: _Concept, lemma_bag: set[str], lemma_bag_full: set[str]) -> str | None:
    for w in concept.words:
        parts = w.split()
        if len(parts) == 1:
            if w in lemma_bag:
                return f"word:{w}"
        else:
            # Use the full (unfiltered) lemma bag for phrases so that short but discriminating
            # parts like "zz" (in "zz top") or "in" (in "alice in chains") are checked.
            if all(p in lemma_bag_full for p in parts):
                return f"phrase:{w}"

    if concept.fuzzy:
        try:
            from rapidfuzz import fuzz
            for fe in concept.fuzzy:
                for lemma in lemma_bag:
                    if fuzz.ratio(fe["lemma"], lemma) >= fe["threshold"]:
                        return f"fuzzy:{lemma}~{fe['lemma']}"
        except ImportError:
            logger.warning("rapidfuzz not installed; fuzzy matching skipped")

    return None