from functools import lru_cache
from typing import Optional

from sqlalchemy import delete, distinct, func, select, text, union
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from rules.english_stemming import is_latin_word, stem_english_word
from rules.normalize import make_morph, normalize
from rules.phonetic import is_cyrillic_word, russian_metaphone
from Storage.models import DescriptionNoteLemma, ImageTag, OCRLemma


@lru_cache(maxsize=1)
def _get_morph():
    return make_morph()


async def _exact_lemma_ids(session: AsyncSession, lemma: str) -> set:
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma == lemma)
    tag_subq = select(distinct(ImageTag.image_id)).where(func.upper(ImageTag.value) == lemma.upper())
    note_subq = select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma == lemma)
    result = await session.execute(union(ocr_subq, tag_subq, note_subq))
    return {row[0] for row in result.all()}


async def _fuzzy_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Trigram-similarity fallback, written to use the pg_trgm GIN index
    (ix_ocr_lemmas_lemma_trgm, and now also ix_description_note_lemmas_lemma_trgm)
    rather than a sequential scan.

    This is deliberately NOT `func.similarity(col, lemma) >= threshold`,
    which looks equivalent but is not: pg_trgm's GIN opclass only
    index-accelerates the `%` operator, not a raw `similarity()`
    function-call predicate — the latter forces a full seq scan
    (confirmed via EXPLAIN ANALYZE against the populated `metal` database:
    ~497ms seq scan vs ~0.3ms bitmap index scan, on 213,981 rows).

    `%`'s notion of "similar enough" is governed by the session GUC
    `pg_trgm.similarity_threshold` (default 0.3), not by an argument we
    pass in, so we set it explicitly via `SET LOCAL` immediately before
    the query to make `%` respect our configured
    settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD instead of pg_trgm's own
    default. `SET LOCAL`'s value cannot be a bound parameter (Postgres
    raises a syntax error), so the threshold is formatted directly into
    the SQL text — safe only because it's a trusted internal config value,
    never user input (unlike `lemma`, which stays a genuine bound
    parameter via `.op("%")(lemma)`). `SET LOCAL` is scoped to the current
    transaction and automatically reverts at its end, which is safe here
    because Storage/db.py's get_async_db keeps exactly one transaction
    open per request, so this can never leak into a later request that
    reuses the same pooled connection.
    """
    threshold = float(settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD)
    assert 0 < threshold <= 1, f"invalid fuzzy similarity threshold: {threshold}"
    await session.execute(text(f"SET LOCAL pg_trgm.similarity_threshold = {threshold}"))
    ocr_subq = select(OCRLemma.image_id).where(OCRLemma.lemma.op("%")(lemma))
    tag_subq = select(distinct(ImageTag.image_id)).where(ImageTag.value.op("%")(lemma))
    note_subq = select(DescriptionNoteLemma.image_id).where(DescriptionNoteLemma.lemma.op("%")(lemma))
    result = await session.execute(union(ocr_subq, tag_subq, note_subq))
    return {row[0] for row in result.all()}


def _is_known_word(lemma: str) -> bool:
    """True if pymorphy3 recognizes lemma via genuine dictionary lookup, as
    opposed to falling back to its unknown-word-guessing analyzer. This is
    what separates erratives (is_known=False) from real dictionary words
    that happen to collide phonetically (is_known=True) -- see the design
    doc for the empirical basis."""
    return bool(_get_morph().parse(lemma)[0].is_known)


async def _phonetic_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Phonetic-code fallback for erratives that trigram similarity cannot
    catch -- see docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md
    for the empirical case against trigram-only and phonetic-only
    approaches. Queries OCRLemma only, not ImageTag: tags come from a
    controlled tagging vocabulary and are essentially never themselves an
    errative string.
    """
    code = russian_metaphone(lemma)
    result = await session.execute(
        select(OCRLemma.image_id).where(OCRLemma.phonetic_code == code)
    )
    return {row[0] for row in result.all()}


async def _stem_lemma_ids(session: AsyncSession, lemma: str) -> set:
    """
    Query-time-only fallback for English word-form variation (e.g. a
    query for "cats" reaching an indexed "cat"). OCRLemmasSaver.add_lemmas()
    already stores the *stemmed* form for "en"-tagged OCR rows (via
    lemmatize_word's STEMMABLE_LANGUAGES branch), so an exact match
    against the query's own stem is enough here -- no separate storage or
    index needed.

    Tried only after exact match already fails (see matching_image_ids),
    mirroring the trigram/phonetic fallback pattern -- NOT baked into the
    primary lemma path, to avoid stemming Spanish (also Latin-script)
    query tokens with English-specific rules and breaking exact match for
    Spanish content that works today. See
    docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.

    OCRLemma only, not ImageTag -- same scope reduction as the phonetic
    fallback (tags are a controlled vocabulary, not raw OCR text).

    Not extended to DescriptionNoteLemma: this fallback only works because
    OCR indexing pre-stems "en"-tagged rows at index time (see
    OCRLemmasSaver/lemmatize_word's STEMMABLE_LANGUAGES branch). Description
    notes have no per-note language tag, so build_description_note_lemmas.py
    indexes with language=None (unstemmed) -- the note-lemma index never
    contains a pre-stemmed form for this tier to find, so adding it here
    would be dead code. Notes still get exact-match and trigram-fuzzy
    fallback coverage; trigram similarity catches most word-form variation
    in practice.
    """
    stem = stem_english_word(lemma)
    result = await session.execute(
        select(OCRLemma.image_id).where(OCRLemma.lemma == stem)
    )
    return {row[0] for row in result.all()}


async def matching_image_ids(session: AsyncSession, q: Optional[str]) -> Optional[set]:
    """
    None means "apply no filter" (q is falsy, or every token normalizes
    away to nothing). Otherwise returns the set of image IDs whose
    OCR-lemma index, description-note-lemma index, or tags contain every
    query lemma (AND); an empty set means no image matches.

    Each query lemma is matched exactly first. If that finds nothing,
    every applicable fallback tier below is unioned together (not tried
    sequentially with early exit) -- they catch different failure classes,
    so there's no reason one should suppress another:

    - If the lemma is Latin-script (is_latin_word), an English-stemming
      fallback (stem_english_word) is tried, with no length guard --
      it's deterministic suffix-stripping, not a similarity search, so it
      doesn't carry the short-word false-positive risk that motivates a
      length guard elsewhere. Deliberately NOT applied to the lemma's
      primary normalization (see rules/normalize.py::lemmatize_word) --
      only as this query-time fallback -- because Spanish is also
      Latin-script and would otherwise get incorrectly stemmed with
      English-specific rules. See
      docs/superpowers/specs/2026-07-26-non-russian-english-lemmatization-design.md.
    - If the lemma is at least settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH
      characters (avoiding short-word false positives — see the design
      doc's empirical similarity-score table), a trigram-similarity
      fallback (settings.SEARCH.FUZZY_SIMILARITY_THRESHOLD) is tried. See
      docs/superpowers/specs/2026-07-24-smart-search-fuzzy-matching-design.md.
    - Additionally, when the lemma is Cyrillic, at least
      settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH characters, and not a
      pymorphy3-recognized dictionary word (_is_known_word is False), a
      phonetic-code fallback is unioned in too -- this catches erratives
      (deliberate misspellings like "превед") that trigram similarity
      cannot. See
      docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md.
    """
    if not q:
        return None

    # language=None enables pymorphy3's script-based fallback (real Cyrillic
    # lemmatization) for a query string, which has no per-word language tag.
    # This is intentionally more thorough than the index side
    # (batch/utils/ocr_lemmas.py), which trusts each OCR row's own detected
    # language and skips lemmatization for confidently-non-Russian rows — see
    # that file's comment for the resulting (accepted) asymmetry.
    lemmas = normalize(
        q, _get_morph(),
        min_length=settings.BOW.MIN_WORD_LENGTH,
        language=None,
        keep_digit_tokens=True,
    )
    if not lemmas:
        return None

    matching_ids: Optional[set] = None
    for lemma in lemmas:
        lemma_ids = await _exact_lemma_ids(session, lemma)
        if not lemma_ids:
            if is_latin_word(lemma):
                lemma_ids = lemma_ids | await _stem_lemma_ids(session, lemma)
            if len(lemma) >= settings.SEARCH.FUZZY_MIN_LEMMA_LENGTH:
                lemma_ids = lemma_ids | await _fuzzy_lemma_ids(session, lemma)
                if (
                    is_cyrillic_word(lemma)
                    and len(lemma) >= settings.SEARCH.PHONETIC_MIN_LEMMA_LENGTH
                    and not _is_known_word(lemma)
                ):
                    lemma_ids = lemma_ids | await _phonetic_lemma_ids(session, lemma)

        matching_ids = lemma_ids if matching_ids is None else (matching_ids & lemma_ids)
        if not matching_ids:
            break

    return matching_ids


class OCRLemmasRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def delete_all(self) -> None:
        """No commit — caller controls commit timing, same convention as
        ImageProcessingStatusRepository.delete_all()/record_failure."""
        print("Deleting all ocr_lemmas rows...")
        await self.session.execute(delete(OCRLemma))
        print("Done")


class OCRLemmasSaver:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.image_count = 0

    async def add_lemmas(self, image_id, lemmas: set) -> None:
        self.image_count += 1
        if not lemmas:
            return
        stmt = (
            insert(OCRLemma)
            .values([
                {
                    "image_id": image_id,
                    "lemma": lemma,
                    "phonetic_code": russian_metaphone(lemma) if is_cyrillic_word(lemma) else None,
                }
                for lemma in lemmas
            ])
            .on_conflict_do_nothing(index_elements=["image_id", "lemma"])
        )
        await self.session.execute(stmt)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        print(f"Total images indexed: {self.image_count}")
        print("Committing...")
        await self.session.commit()
        print("Done")
