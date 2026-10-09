"""Turn stored ratings into gold-set review files (P5b). Pure: no database, no files, no clock."""
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from evaluation.gold import GoldQuery, Judgment
from retrieval.query_hash import normalised_query, query_hash

RATER = "rating-ui"


@dataclass(frozen=True)
class ExportResult:
    judgments_by_language: dict[str, dict]
    candidates: dict
    report: dict


def _summary(ratings: Sequence) -> dict:
    return {
        "total": len(ratings),
        "byGrade": {str(grade): sum(1 for r in ratings if r.grade == grade) for grade in (0, 1, 2)},
        "byLanguage": dict(sorted(Counter(r.language for r in ratings).items())),
        "byReading": dict(sorted(Counter(r.reading for r in ratings).items())),
        "degraded": sum(1 for r in ratings if r.degraded),
        "byParserVersion": dict(sorted(Counter(r.parser_version for r in ratings).items())),
    }


def build_export(ratings: Iterable, queries: Sequence[GoldQuery], judgments: Sequence[Judgment],
                 corpus_image_ids: set[str], *, max_chars: int, round_number: int, today: str,
                 rater: str = RATER) -> ExportResult:
    ratings = sorted(ratings, key=lambda r: (r.query_hash, str(r.image_id)))
    gold_by_hash = {query_hash(normalised_query(q.text, max_chars)): q for q in queries}
    existing = {(j.query_id, j.image_id): j.grade for j in judgments}

    importable: dict[str, list[dict]] = defaultdict(list)
    already_judged, outside_corpus = [], []
    unknown: dict[str, list] = defaultdict(list)

    for rating in ratings:
        image_id = str(rating.image_id)
        gold = gold_by_hash.get(rating.query_hash)
        if gold is None:
            unknown[rating.query_hash].append(rating)
        elif image_id not in corpus_image_ids:
            outside_corpus.append({"queryId": gold.id, "imageId": image_id, "grade": rating.grade})
        elif (gold.id, image_id) in existing:
            already = existing[(gold.id, image_id)]
            already_judged.append({"queryId": gold.id, "imageId": image_id, "existingGrade": already,
                                   "ratingGrade": rating.grade, "agree": already == rating.grade})
        else:
            importable[gold.language].append({"queryId": gold.id, "imageId": image_id, "grade": rating.grade})

    judgments_by_language = {
        language: {"rater": rater, "date": today, "round": round_number, "language": language,
                   "judgments": sorted(entries, key=lambda e: (e["queryId"], e["imageId"]))}
        for language, entries in sorted(importable.items())
    }

    candidates = []
    for digest, rows in sorted(unknown.items()):
        text = next((r.query_text for r in rows if r.query_text), None)
        language = Counter(r.language for r in rows).most_common(1)[0][0]
        candidates.append({
            "queryHash": digest, "text": text, "language": language,
            "ratings": [{"imageId": str(r.image_id), "grade": r.grade, "rank": r.rank, "reading": r.reading,
                         "parserVersion": r.parser_version, "degraded": r.degraded} for r in rows],
        })

    report = {
        "summary": _summary(ratings),
        "importable": sum(len(v["judgments"]) for v in judgments_by_language.values()),
        "alreadyJudged": already_judged,
        "outsideCorpus": outside_corpus,
        "candidateMessages": len(candidates),
    }
    return ExportResult(judgments_by_language, {"version": 1, "candidates": candidates}, report)
