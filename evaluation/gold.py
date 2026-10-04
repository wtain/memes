"""Gold data formats for the meme-from-text evaluation (P6 spec, decision 2). Pure: standard library plus taxonomy.

All files are UTF-8 with LF newlines, a fixed key order and a trailing newline, so they diff cleanly in git
and hash identically on Windows and Linux checkouts.
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile

FORMAT_VERSION = 1
LANGUAGES = ("en", "ru", "es")
LENGTHS = ("short", "medium", "long")
DIFFICULTIES = ("easy", "hard", "ambiguous")
STRATA = ("ocr+description", "ocr only", "description only", "no usable text")
GRADES = (0, 1, 2)
CONTROLLED_AXES = ("emotion", "stance", "topic", "tone")
MAX_QUERY_CHARS = 600
WORD_LIMITS = {"short": (1, 8), "medium": (9, 20), "long": (21, 10_000)}


class GoldError(ValueError):
    pass


@dataclass(frozen=True)
class CorpusImage:
    image_id: str
    content_hash: str | None
    stratum: str


@dataclass(frozen=True)
class Corpus:
    environment: str
    seed: int
    created: str
    images: tuple[CorpusImage, ...]
    version: int = FORMAT_VERSION

    def image_ids(self) -> tuple[str, ...]:
        return tuple(image.image_id for image in self.images)

    def strata(self) -> dict[str, str]:
        return {image.image_id: image.stratum for image in self.images}


@dataclass(frozen=True)
class ExpectedProfile:
    situation: str
    emotion: tuple[str, ...]
    stance: tuple[str, ...]
    topic: tuple[str, ...]
    tone: tuple[str, ...]

    def axis_values(self) -> dict[str, tuple[str, ...]]:
        return {axis: getattr(self, axis) for axis in CONTROLLED_AXES}


@dataclass(frozen=True)
class GoldQuery:
    id: str
    language: str
    text: str
    length: str
    difficulty: str
    expected: ExpectedProfile
    reviewed: bool = False
    pair_id: str | None = None


@dataclass(frozen=True)
class Judgment:
    query_id: str
    image_id: str
    grade: int
    rater: str
    date: str
    round: int


@dataclass(frozen=True)
class MergeResult:
    merged: list[Judgment]
    added: int
    overwritten: int
    unchanged: int


# ---------------------------------------------------------------- serialisation helpers

def _check_version(data: Mapping, what: str) -> None:
    if data.get("version") != FORMAT_VERSION:
        raise GoldError(f"{what}: unsupported version {data.get('version')!r} (expected {FORMAT_VERSION})")


def dump_json(data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _write(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(dump_json(data))


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def file_sha256(path: Path) -> str:
    """sha256 of the file content with CRLF normalised to LF (stable across Windows and Linux checkouts)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------- corpus

def corpus_to_dict(corpus: Corpus) -> dict:
    return {
        "version": corpus.version,
        "environment": corpus.environment,
        "seed": corpus.seed,
        "created": corpus.created,
        "images": [{"imageId": i.image_id, "contentHash": i.content_hash, "stratum": i.stratum}
                   for i in corpus.images],
    }


def parse_corpus(data: Mapping) -> Corpus:
    _check_version(data, "corpus")
    images, seen = [], set()
    for entry in data.get("images", []):
        if not isinstance(entry, dict):
            raise GoldError(f"corpus: image entry {len(images)} is not an object")
        image_id = entry.get("imageId")
        if not isinstance(image_id, str) or not image_id:
            raise GoldError("corpus: every image needs an imageId")
        if image_id in seen:
            raise GoldError(f"corpus: duplicate image id {image_id}")
        seen.add(image_id)
        if entry.get("stratum") not in STRATA:
            raise GoldError(f"corpus: image {image_id} has an unknown stratum {entry.get('stratum')!r}")
        images.append(CorpusImage(image_id, entry.get("contentHash"), entry["stratum"]))
    for key in ("environment", "seed", "created"):
        if key not in data:
            raise GoldError(f"corpus: missing {key}")
    return Corpus(environment=data["environment"], seed=data["seed"], created=data["created"],
                  images=tuple(images), version=data["version"])


def load_corpus(path: Path) -> Corpus:
    return parse_corpus(_read(Path(path)))


def save_corpus(corpus: Corpus, path: Path) -> None:
    _write(Path(path), corpus_to_dict(corpus))


# ---------------------------------------------------------------- queries

def queries_to_dict(queries: Sequence[GoldQuery]) -> dict:
    items = []
    for q in queries:
        item = {
            "id": q.id, "language": q.language, "text": q.text,
            "tags": {"length": q.length, "difficulty": q.difficulty},
            "expectedProfile": {"situation": q.expected.situation, **{a: list(v) for a, v in q.expected.axis_values().items()}},
            "reviewed": q.reviewed,
        }
        if q.pair_id is not None:
            item["pairId"] = q.pair_id
        items.append(item)
    return {"version": FORMAT_VERSION, "queries": items}


def parse_queries(data: Mapping) -> list[GoldQuery]:
    _check_version(data, "queries")
    queries = []
    for index, entry in enumerate(data.get("queries", [])):
        if not isinstance(entry, dict):
            raise GoldError(f"queries: entry #{index} is not an object")
        name = entry.get("id", f"#{index}")
        profile = entry.get("expectedProfile")
        if not isinstance(profile, dict):
            raise GoldError(f"{name}: expectedProfile is required")
        tags = entry.get("tags") or {}
        try:
            queries.append(GoldQuery(
                id=entry["id"], language=entry["language"], text=entry["text"],
                length=tags.get("length", ""), difficulty=tags.get("difficulty", ""),
                expected=ExpectedProfile(
                    situation=profile.get("situation", ""),
                    **{axis: tuple(profile.get(axis, [])) for axis in CONTROLLED_AXES}),
                reviewed=bool(entry.get("reviewed", False)), pair_id=entry.get("pairId")))
        except KeyError as error:
            raise GoldError(f"queries: entry {name} is missing {error.args[0]}") from None
        except (TypeError, AttributeError) as error:
            raise GoldError(f"queries: entry {name} is malformed ({error})") from None
    return queries


def load_queries(path: Path) -> list[GoldQuery]:
    return parse_queries(_read(Path(path)))


def save_queries(queries: Sequence[GoldQuery], path: Path) -> None:
    _write(Path(path), queries_to_dict(queries))


def check_queries(queries: Sequence[GoldQuery], tax: Taxonomy) -> list[str]:
    """Structural problems of every query (empty list = fine)."""
    problems: list[str] = []
    seen: set[str] = set()
    for q in queries:
        if q.id in seen:
            problems.append(f"duplicate query id {q.id}")
        seen.add(q.id)
        if not q.text.strip():
            problems.append(f"{q.id}: text is empty")
        elif len(q.text) > MAX_QUERY_CHARS:
            problems.append(f"{q.id}: text is longer than {MAX_QUERY_CHARS} characters")
        if q.language not in LANGUAGES:
            problems.append(f"{q.id}: language {q.language!r} is not one of {LANGUAGES}")
        if q.length not in LENGTHS:
            problems.append(f"{q.id}: length {q.length!r} is not one of {LENGTHS}")
        if q.difficulty not in DIFFICULTIES:
            problems.append(f"{q.id}: difficulty {q.difficulty!r} is not one of {DIFFICULTIES}")
        if not q.expected.situation.strip():
            problems.append(f"{q.id}: situation is empty")
        profile = {"situation": q.expected.situation.strip() or None,
                   **{axis: [{"key": key} for key in values] for axis, values in q.expected.axis_values().items()}}
        result = validate_profile(profile, tax)
        problems.extend(f"{q.id}: {error}" for error in result.errors)
    return problems


def lint_query_set(queries: Sequence[GoldQuery], tax: Taxonomy, *, per_language: int = 20,
                   length_quota: Mapping[str, int] | None = None, difficulty_quota: Mapping[str, int] | None = None,
                   min_stances: int = 8, min_emotions: int = 10, min_tones: int = 6,
                   min_pairs: int = 6) -> list[str]:
    """The drafting rules of P6 spec decision 4 (empty list = the set is acceptable)."""
    length_quota = dict(length_quota or {"short": 5, "medium": 10, "long": 5})
    difficulty_quota = dict(difficulty_quota or {"easy": 7, "hard": 8, "ambiguous": 5})
    problems: list[str] = []

    for language in LANGUAGES:
        group = [q for q in queries if q.language == language]
        if len(group) != per_language:
            problems.append(f"{language}: {len(group)} queries, expected {per_language}")
        for tag, quota, getter in (("length", length_quota, lambda q: q.length),
                                   ("difficulty", difficulty_quota, lambda q: q.difficulty)):
            for value, expected in quota.items():
                have = sum(1 for q in group if getter(q) == value)
                if have != expected:
                    problems.append(f"{language}: {have} queries with {tag} {value}, expected {expected}")

    for q in queries:
        low, high = WORD_LIMITS.get(q.length, (0, 0))
        words = len(q.text.split())
        if not low <= words <= high:
            problems.append(f"{q.id}: length tag {q.length} but {words} words (allowed {low}-{high})")

    for axis, minimum in (("topic", len(tax.values["topic"])), ("stance", min_stances),
                          ("emotion", min_emotions), ("tone", min_tones)):
        distinct = {key for q in queries for key in getattr(q.expected, axis)}
        if len(distinct) < minimum:
            problems.append(f"only {len(distinct)} distinct {axis} keys across the expected profiles, need {minimum}")

    languages_by_pair: dict[str, set[str]] = {}
    for q in queries:
        if q.pair_id:
            languages_by_pair.setdefault(q.pair_id, set()).add(q.language)
    complete = sum(1 for languages in languages_by_pair.values() if languages == set(LANGUAGES))
    if complete < min_pairs:
        problems.append(f"only {complete} pair ids appear in all three languages, need {min_pairs}")
    return problems


# ---------------------------------------------------------------- judgments

def judgments_to_dict(judgments: Sequence[Judgment]) -> dict:
    ordered = sorted(judgments, key=lambda j: (j.query_id, j.image_id))
    return {"version": FORMAT_VERSION,
            "judgments": [{"queryId": j.query_id, "imageId": j.image_id, "grade": j.grade, "rater": j.rater,
                           "date": j.date, "round": j.round} for j in ordered]}


def parse_judgments(data: Mapping) -> list[Judgment]:
    _check_version(data, "judgments")
    judgments = []
    for index, entry in enumerate(data.get("judgments", [])):
        if not isinstance(entry, dict):
            raise GoldError(f"judgments: entry #{index} is not an object")
        name = f"{entry.get('queryId', '#' + str(index))}/{entry.get('imageId', '?')}"
        if entry.get("grade") not in GRADES:
            raise GoldError(f"judgment {name}: grade must be one of {GRADES}")
        try:
            judgments.append(Judgment(entry["queryId"], entry["imageId"], entry["grade"], entry["rater"],
                                      entry["date"], entry["round"]))
        except KeyError as error:
            raise GoldError(f"judgment {name}: missing {error.args[0]}") from None
    return judgments


def load_judgments(path: Path) -> list[Judgment]:
    return parse_judgments(_read(Path(path)))


def save_judgments(judgments: Sequence[Judgment], path: Path) -> None:
    _write(Path(path), judgments_to_dict(judgments))


def merge_judgments(existing: Sequence[Judgment], incoming: Iterable[Judgment]) -> MergeResult:
    """Merge by (query, image). A later grade overwrites an earlier one; an identical grade is 'unchanged'."""
    by_pair = {(j.query_id, j.image_id): j for j in existing}
    added = overwritten = unchanged = 0
    for judgment in incoming:
        key = (judgment.query_id, judgment.image_id)
        previous = by_pair.get(key)
        if previous is None:
            added += 1
            by_pair[key] = judgment
        elif previous.grade == judgment.grade:
            unchanged += 1
        else:
            overwritten += 1
            by_pair[key] = judgment
    return MergeResult(list(by_pair.values()), added, overwritten, unchanged)


def judgments_by_query(judgments: Iterable[Judgment]) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {}
    for judgment in judgments:
        grouped.setdefault(judgment.query_id, {})[judgment.image_id] = judgment.grade
    return grouped


def parse_export(data: Mapping, *, valid_query_ids: set[str], valid_image_ids: set[str]) -> list[Judgment]:
    """Validate a judging-sheet export (`{rater, date, round, judgments: [{queryId, imageId, grade}]}`)."""
    for field in ("rater", "date", "round"):
        if field not in data:
            raise GoldError(f"export: missing {field}")
    judgments, seen = [], set()
    for index, entry in enumerate(data.get("judgments", [])):
        if not isinstance(entry, dict):
            raise GoldError(f"export: entry #{index} is not an object")
        query_id, image_id, grade = entry.get("queryId"), entry.get("imageId"), entry.get("grade")
        if query_id not in valid_query_ids:
            raise GoldError(f"export: unknown query {query_id!r}")
        if image_id not in valid_image_ids:
            raise GoldError(f"export: unknown image {image_id!r}")
        if grade not in GRADES:
            raise GoldError(f"export: grade for {query_id}/{image_id} must be one of {GRADES}")
        if (query_id, image_id) in seen:
            raise GoldError(f"export: duplicate judgment for {query_id}/{image_id}")
        seen.add((query_id, image_id))
        judgments.append(Judgment(query_id, image_id, grade, data["rater"], data["date"], data["round"]))
    return judgments
