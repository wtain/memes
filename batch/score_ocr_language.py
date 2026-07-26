"""
Backfill lang_score for existing ocr_texts rows (added by
Storage/alembic/versions/2026_07_03_add_ocr_lang_score.py). New rows are
scored automatically going forward by repository/ocr_text.py::overwrite_texts
— this script is only needed for rows written before that change, or to
recompute every row after tuning rules/lang_plausibility.py's constants.

Usage:
    python -m batch.score_ocr_language                # scores rows where lang_score IS NULL
    python -m batch.score_ocr_language --rescore-all   # recomputes lang_score for every row
"""
import argparse
import asyncio

from Storage.db import AsyncSessionLocal
from repository.ocr_text import OCRTextRepository
from rules.lang_plausibility import score

COMMIT_EVERY = 500


async def run(rescore_all: bool = False) -> None:
    async with AsyncSessionLocal() as session:
        repo = OCRTextRepository(session)
        rows = await repo.get_rows_for_scoring(rescore_all=rescore_all)
        print(f"Rows to score: {len(rows)}")

        scored = 0
        batch = []
        for row in rows:
            lang_score = score(row.text, row.language)
            batch.append({"b_id": row.id, "lang_score": lang_score})
            scored += 1
            if len(batch) >= COMMIT_EVERY:
                await repo.update_lang_scores(batch)
                await session.commit()
                batch = []
                print(f"  scored {scored}/{len(rows)}")

        if batch:
            await repo.update_lang_scores(batch)
            await session.commit()

        print(f"Done. Scored {scored} rows.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rescore-all",
        action="store_true",
        help="Recompute lang_score for every row, not just unscored ones",
    )
    args = parser.parse_args()
    asyncio.run(run(rescore_all=args.rescore_all))


if __name__ == "__main__":
    main()
