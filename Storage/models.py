from datetime import datetime
import enum
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index, Boolean,
    BigInteger, UniqueConstraint, CheckConstraint, text
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
import uuid


Base = declarative_base()

EMBEDDING_DIM = 512

TEXT_EMBEDDING_DIM = 1024

# Output size of the Ollama-served model behind meme profile vectors (P1 spec, decision 6).
# Measured with batch/tools/probe_embedding_model.py. Independent of TEXT_EMBEDDING_DIM: the
# vectors come from a different runtime and must never share a table or index with it.
PROFILE_EMBEDDING_DIM = 1024


class ImageStatus:
    """Values of images.status (a free String(20), deliberately not an enum or a check constraint).
    Meaning, who sets each value, who reads it and the allowed transitions: docs/image-status.md,
    which is authoritative."""
    ACTIVE = "active"
    PENDING = "pending"
    REJECTED = "rejected"
    CUT_PROPOSED = "cut_proposed"
    ALL = (ACTIVE, PENDING, REJECTED, CUT_PROPOSED)


class CutState:
    """Values of image_cuts.state (epic screenshot-recut). LIVE states hold the one-per-source slot."""
    DETECTED = "detected"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"
    LIVE = (DETECTED, PROPOSED, APPROVED)
    ALL = (DETECTED, PROPOSED, APPROVED, REJECTED, FAILED)


class StaleMixin:
    """Marks a derived row that was copied from a source image to its cut and must be rewritten by
    the 'refresh stale' mode of the job that owns the table (epic screenshot-recut, ADR-001).
    Only tables whose jobs select work by absence of rows carry it; see
    docs/image-derived-data.md. Rewritten rows take the default (False), so nothing needs clearing."""
    stale = Column(Boolean, nullable=False, server_default=sa.false(), default=False)



class Image(Base):
    __tablename__ = "images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False, index=True)
    content_hash = Column(String, nullable=True, index=True)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())
    status = Column(String(20), nullable=False, server_default="active")
    ingestion_batch_id = Column(
        UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True
    )
    # Copies of this meme seen in total (epic meme-popularity): a cache of the absorption tree below it.
    popularity = Column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        Index(
            "ix_images_created_at_id_desc",
            created_at.desc(),
            id.desc()
        ),
        # Partial index sized to the small pending minority, not the whole (overwhelmingly
        # active) table -- see 2026-07-25-image-visibility-status-design.md.
        Index(
            "ix_images_status_pending",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        CheckConstraint("popularity >= 0", name="ck_images_popularity"),
    )

    texts = relationship("OCRText", back_populates="image", cascade="all, delete-orphan")
    descriptions = relationship("ImageDescription", back_populates="image", cascade="all, delete-orphan")
    metrics = relationship("ImageMetrics", uselist=False, back_populates="image")
    errors = relationship("ProcessingError", back_populates="image")
    embeddings = relationship("Embedding", back_populates="image", cascade="all, delete-orphan")
    tags = relationship("ImageTag", back_populates="image", cascade="all, delete-orphan")
    ocr_lemmas = relationship("OCRLemma", back_populates="image", cascade="all, delete-orphan")
    image_extras = relationship("ImageExtras", back_populates="image", cascade="all, delete-orphan")
    description_note = relationship(
        "DescriptionNote", uselist=False, back_populates="image", cascade="all, delete-orphan"
    )
    description_note_lemmas = relationship(
        "DescriptionNoteLemma", back_populates="image", cascade="all, delete-orphan"
    )
    classifications = relationship("ImageClassification", back_populates="image", cascade="all, delete-orphan")
    ocr_text_embedding = relationship(
        "OCRTextEmbedding", uselist=False, back_populates="image", cascade="all, delete-orphan"
    )


_CUT_LIVE_PREDICATE = "state IN ('detected', 'proposed', 'approved')"


class ImageCut(Base):
    """One proposal to replace a badly cropped image (source) with a tighter cut (epic screenshot-recut).
    The record outlives both images: the three foreign keys are SET NULL, so a removed source or a rejected
    and removed cut leaves the history (box, detector, state) behind, with source_content_hash as the audit
    anchor. At most one live (detected/proposed/approved) row per source; rejected and failed rows may
    repeat. The box is in source pixels and is validated against the source size in code, not here."""
    __tablename__ = "image_cuts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    cut_image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    source_content_hash = Column(String, nullable=True)
    crop_x = Column(Integer, nullable=False)
    crop_y = Column(Integer, nullable=False)
    crop_w = Column(Integer, nullable=False)
    crop_h = Column(Integer, nullable=False)
    detector = Column(String(50), nullable=False)
    detector_version = Column(String(50), nullable=False)
    confidence = Column(Float, nullable=True)
    detect_run_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True)
    state = Column(String(20), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)
    # Why a detected proposal could not be cut (R3a): source_missing, source_changed, unreadable, write_error.
    failure_reason = Column(String(40), nullable=True)

    __table_args__ = (
        Index("uq_image_cuts_live_source", "source_image_id", unique=True,
              postgresql_where=text(_CUT_LIVE_PREDICATE)),
        Index("uq_image_cuts_cut_image_id", "cut_image_id", unique=True),
        Index("ix_image_cuts_detect_run_id", "detect_run_id"),
        Index("ix_image_cuts_state", "state"),
        CheckConstraint("crop_x >= 0 AND crop_y >= 0 AND crop_w > 0 AND crop_h > 0", name="ck_image_cuts_box"),
        CheckConstraint("state IN ('detected', 'proposed', 'approved', 'rejected', 'failed')",
                        name="ck_image_cuts_state"),
    )


class PopularitySource:
    """Values of popularity_absorptions.source (epic meme-popularity)."""
    HASH_IN_BATCH = "hash_in_batch"
    HASH_CROSS_CORPUS = "hash_cross_corpus"
    INGEST_TIER_A = "ingest_tier_a"
    INGEST_TIER_B = "ingest_tier_b"
    CLUSTER_REVIEW = "cluster_review"
    AGENT_REVIEW = "agent_review"
    FILE_DUPLICATES = "file_duplicates"
    RECUT = "recut"
    ALL = (HASH_IN_BATCH, HASH_CROSS_CORPUS, INGEST_TIER_A, INGEST_TIER_B,
           CLUSTER_REVIEW, AGENT_REVIEW, FILE_DUPLICATES, RECUT)


class PopularityKind:
    """Values of popularity_absorptions.kind."""
    ABSORB = "absorb"
    REVERSE = "reverse"
    ALL = (ABSORB, REVERSE)


class PopularityReason:
    """Values of popularity_absorptions.reason (reverse rows only)."""
    UNDO_REJECT = "undo_reject"
    UNFLAG = "unflag"
    ABORT = "abort"
    CORRECTION = "correction"
    ALL = (UNDO_REJECT, UNFLAG, ABORT, CORRECTION)


def _in_list(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PopularityAbsorption(Base):
    """Append-only log of duplicate absorptions (epic meme-popularity); only reversed_by_id of an absorb row
    is ever updated. Deliberately no foreign key to images: ids outlive deleted images so trees and audit
    survive deletion."""
    __tablename__ = "popularity_absorptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(10), nullable=False)
    source = Column(String(30), nullable=False)
    loser_image_id = Column(UUID(as_uuid=True), nullable=True)
    winner_image_id = Column(UUID(as_uuid=True), nullable=False)
    weight = Column(Integer, nullable=False)
    loser_filename = Column(String, nullable=True)
    loser_content_hash = Column(String, nullable=True)
    winner_content_hash = Column(String, nullable=True)
    dedupe_key = Column(String, nullable=True)
    batch_run_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True)
    reverses_id = Column(UUID(as_uuid=True), ForeignKey("popularity_absorptions.id"), nullable=True)
    reversed_by_id = Column(UUID(as_uuid=True), ForeignKey("popularity_absorptions.id"), nullable=True)
    reason = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_popularity_dedupe_key"),
        UniqueConstraint("reverses_id", name="uq_popularity_reverses_id"),
        Index("uq_popularity_open_loser", "loser_image_id", unique=True,
              postgresql_where=text("kind = 'absorb' AND reversed_by_id IS NULL AND loser_image_id IS NOT NULL")),
        Index("ix_popularity_open_winner", "winner_image_id",
              postgresql_where=text("kind = 'absorb' AND reversed_by_id IS NULL")),
        Index("ix_popularity_batch_run_id", "batch_run_id"),
        CheckConstraint(_in_list("kind", PopularityKind.ALL), name="ck_popularity_absorptions_kind"),
        CheckConstraint("weight >= 0", name="ck_popularity_absorptions_weight"),
        CheckConstraint(_in_list("source", PopularitySource.ALL), name="ck_popularity_absorptions_source"),
        CheckConstraint(f"reason IS NULL OR {_in_list('reason', PopularityReason.ALL)}",
                        name="ck_popularity_absorptions_reason"),
        CheckConstraint(
            "(kind = 'absorb' AND reverses_id IS NULL AND reason IS NULL) OR "
            "(kind = 'reverse' AND reverses_id IS NOT NULL AND reason IS NOT NULL)",
            name="ck_popularity_absorptions_shape"),
    )


class ProcessEvent(Base):
    """One outcome of a process on one subject, kept forever (epic screenshot-recut, ADR-002). Append-only:
    rates are computed from these rows at read time. A subject has each event at most once, so recording is
    idempotent. batch_run_id is the run that produced the subject; if that run is pruned the event stays,
    unattributed. subject_id has no foreign key because subjects differ per process (image_cuts.id for recut)."""
    __tablename__ = "process_events"
    __table_args__ = (
        UniqueConstraint("process", "event", "subject_id", name="uq_process_events_subject_event"),
        Index("ix_process_events_process_run_event", "process", "batch_run_id", "event"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    process = Column(String(40), nullable=False)
    event = Column(String(40), nullable=False)
    batch_run_id = Column(UUID(as_uuid=True), ForeignKey("batch_runs.run_id", ondelete="SET NULL"), nullable=True)
    subject_id = Column(UUID(as_uuid=True), nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ImageMetrics(Base):
    __tablename__ = "image_metrics"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True, index=True)

    read_time_ms = Column(Numeric)
    preprocess_time_ms = Column(Numeric)
    ocr_time_ms = Column(Numeric)
    total_time_ms = Column(Numeric)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="metrics")


class OCRText(StaleMixin, Base):
    __tablename__ = "ocr_texts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    text = Column(Text, nullable=False)
    confidence = Column(Float)
    bbox = Column(JSON)            # polygon or x,y,w,h
    language = Column(String(8), default="en")
    lang_score = Column(Float, nullable=True)  # None = not scored (too short); else 0.0-1.0

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="texts")


class ImageDescription(StaleMixin, Base):
    __tablename__ = "image_descriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    prompt_key = Column(String, nullable=False)
    model_used = Column(String, nullable=False)
    text = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id", "prompt_key", name="uq_image_description_image_prompt"),
    )

    image = relationship("Image", back_populates="descriptions")
    embedding = relationship(
        "ImageDescriptionEmbedding", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )
    feedback = relationship(
        "ImageDescriptionFeedback", uselist=False,
        back_populates="description", cascade="all, delete-orphan",
    )


class ImageDescriptionEmbedding(Base):
    __tablename__ = "image_description_embeddings"

    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding = Column(Vector(TEXT_EMBEDDING_DIM))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_image_description_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    description = relationship("ImageDescription", back_populates="embedding")


class ImageDescriptionFeedback(Base):
    __tablename__ = "image_description_feedback"

    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    approved = Column(Boolean, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    description = relationship("ImageDescription", back_populates="feedback")


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    embedding = Column(Vector(EMBEDDING_DIM))

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_embeddings_embedding_hnsw_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    image = relationship("Image", back_populates="embeddings")

class TmpDuplicates(Base):
    """Candidate duplicate pairs found by rebuild_duplicates.py's incremental,
    HNSW-assisted KNN search -- see
    docs/superpowers/specs/2026-07-25-duplicate-clustering-incremental-design.md.
    A real, migration-managed table (not script-created DDL) so inserts can be
    incremental and idempotent via the (image_id1, image_id2) unique constraint;
    image_id1/image_id2 are always stored as (LEAST(a, b), GREATEST(a, b)) so a
    pair is only ever represented once, not twice as (a, b) and (b, a)."""

    __tablename__ = "tmp_duplicates"

    # server_default matters here, unlike most other models' id columns -- rows are
    # inserted via raw SQL (INSERT ... SELECT ... ON CONFLICT), not constructed as ORM
    # objects, so there's no Python-side default to fall back on.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"), index=True)
    image_id1 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)
    image_id2 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)

    distance = Column(Float, nullable=False)

    # 'in_batch' | 'cross_corpus' -- which side of an ingestion review query this pair
    # came from; always 'cross_corpus' for active-library rebuild rows (probe and corpus
    # are both status='active'). Ingestion-specific, unused by the active-library rebuild
    # beyond always populating it via the same query.
    match_source = Column(String(20), nullable=True)

    # 'clip' | 'ocr_text' -- which embedding signal produced this row's distance. Orthogonal to
    # match_source (in_batch/cross_corpus): a pair can be any combination of the two. Existing
    # rows (all CLIP-sourced, from before this column existed) default to 'clip'. See
    # docs/superpowers/specs/2026-09-18-text-embedding-duplicate-matching.md.
    distance_source = Column(String(20), nullable=False, server_default="clip")

    # Ingestion-specific, tier-scoped review-queue resumability markers -- set only by an
    # explicit "not a duplicate" decision in that tier's review UI. Never set by the
    # active-library rebuild. See 2026-07-24-ingestion-pipeline-design.md's
    # "Review-queue resumability" section.
    tier_a_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    tier_b_reviewed_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id1", "image_id2", name="uq_tmp_duplicates_pair"),
        Index("idx_tmp_duplicates_distance", "distance"),
        Index(
            "idx_tmp_duplicates_tier_b_unreviewed_distance",
            "distance",
            postgresql_where=text("tier_b_reviewed_at IS NULL"),
        ),
    )


class TmpImageClusters(Base):
    __tablename__ = "tmp_clusters"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)


class DuplicateDecision(Base):
    """A human-confirmed "these two images are not duplicates" decision. Durable source
    data -- unlike tmp_duplicates/tmp_clusters, this table is never dropped or wiped by
    any batch script, including rebuild_duplicates.py's --full mode. clusterize.py
    excludes any pair present here from its union-find. See
    docs/superpowers/specs/2026-08-19-duplicate-dismissal-decisions-design.md.

    image_id1/image_id2 are always stored as (LEAST(a, b), GREATEST(a, b)), mirroring
    TmpDuplicates' own convention, so a pair is only ever represented once."""

    __tablename__ = "duplicate_decisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                server_default=text("gen_random_uuid()"), index=True)
    image_id1 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)
    image_id2 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False, index=True)

    decided_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("image_id1", "image_id2", name="uq_duplicate_decisions_pair"),
    )


class ImageTag(StaleMixin, Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    key = Column(String)
    value = Column(String)
    source = Column(String)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="tags")


class OCRLemma(StaleMixin, Base):
    __tablename__ = "ocr_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)
    # Nullable by design: only OCRLemmasSaver.add_lemmas() (the real write
    # path) populates it; rows created directly for tests unrelated to
    # phonetic matching are correctly inert with phonetic_code=NULL (NULL
    # never equals anything in SQL, so they never participate in phonetic
    # lookups). See docs/superpowers/specs/2026-07-25-smart-search-phonetic-erratives-design.md
    # for why this isn't a NOT NULL column or an ORM @validates hook.
    phonetic_code = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_ocr_lemmas_lemma", "lemma"),
        Index(
            "ix_ocr_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
        Index("ix_ocr_lemmas_phonetic_code", "phonetic_code"),
    )

    image = relationship("Image", back_populates="ocr_lemmas")


class Concept(Base):
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    embedding = Column(Vector(EMBEDDING_DIM))  # Deprecate

    image_sets = relationship("ConceptImageSet", back_populates="concept")
    text_sets = relationship("ConceptTextSet", back_populates="concept")


# -----------------------
# IMAGE SIDE
# -----------------------

class ConceptImageSet(Base):
    __tablename__ = "concept_image_sets"

    id = Column(Integer, primary_key=True)
    concept_id = Column(Integer, ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)

    name = Column(String)
    directory = Column(String)

    centroid_embedding = Column(Vector(EMBEDDING_DIM))

    concept = relationship("Concept", back_populates="image_sets")
    images = relationship("ConceptImage", back_populates="image_set")


class ConceptImage(Base):
    __tablename__ = "concept_images"

    id = Column(Integer, primary_key=True)
    concept_image_set_id = Column(Integer, ForeignKey("concept_image_sets.id", ondelete="CASCADE"), nullable=False)

    filename = Column(String, nullable=False)

    embedding = Column(Vector(EMBEDDING_DIM))

    image_set = relationship("ConceptImageSet", back_populates="images")


# -----------------------
# TEXT SIDE
# -----------------------

class ConceptTextSet(Base):
    __tablename__ = "concept_text_sets"

    id = Column(Integer, primary_key=True)
    concept_id = Column(Integer, ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)

    name = Column(String)

    centroid_embedding = Column(Vector(EMBEDDING_DIM))

    concept = relationship("Concept", back_populates="text_sets")
    texts = relationship("ConceptText", back_populates="text_set")


class ConceptText(Base):
    __tablename__ = "concept_texts"

    id = Column(Integer, primary_key=True)
    concept_text_set_id = Column(Integer, ForeignKey("concept_text_sets.id", ondelete="CASCADE"), nullable=False)

    name = Column(String)
    text = Column(String, nullable=False)

    embedding = Column(Vector(EMBEDDING_DIM))

    text_set = relationship("ConceptTextSet", back_populates="texts")


class ProcessingError(Base):
    __tablename__ = "processing_errors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    stage = Column(String, nullable=False)   # read | preprocess | ocr | persist
    message = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="errors")


class ImageProcessingStatus(Base):
    __tablename__ = "image_processing_status"

    image_id = Column(
        UUID(as_uuid=True),
        ForeignKey("images.id", ondelete="CASCADE"),
        primary_key=True
    )

    pipeline = Column(String, primary_key=True)
    # e.g. "easyocr:en"

    status = Column(String, nullable=False)
    # processing | done | failed

    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    error_message = Column(Text)

    image = relationship("Image")


class ImageExtras(Base):
    __tablename__ = "image_extras"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True, index=True)

    flagged = Column(Boolean)
    flagged_reason = Column(String(50), nullable=True)
    remarks = Column(Text)

    image = relationship("Image", back_populates="image_extras")


class ImageClassification(Base):
    __tablename__ = "image_classifications"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    classifier = Column(String, primary_key=True)
    result = Column(String, nullable=False)
    details = Column(JSON)
    computed_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="classifications")


OCR_TEXT_EMBEDDING_DIM = 384  # paraphrase-multilingual-MiniLM-L12-v2 -- verified empirically
                               # (SbertModel().embed_text(...).shape == (384,)), not
                               # bge-large-en-v1.5's 1024 (TEXT_EMBEDDING_DIM).


class OCRTextEmbedding(Base):
    __tablename__ = "ocr_text_embeddings"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(Vector(OCR_TEXT_EMBEDDING_DIM))
    computed_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="ocr_text_embedding")

    __table_args__ = (
        Index(
            "ix_ocr_text_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class DescriptionNote(Base):
    __tablename__ = "description_notes"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    text = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, server_default=func.now())
    # Staleness markers for the two batch jobs (build_description_note_lemmas /
    # build_description_note_embeddings): a note can be edited repeatedly after
    # creation (unlike ImageDescription, which is never edited), so "row exists"
    # alone isn't enough to know a lemma/embedding is up to date -- each job
    # reindexes when its built_at is NULL or older than updated_at.
    lemmas_built_at = Column(DateTime, nullable=True)
    embedding_built_at = Column(DateTime, nullable=True)

    image = relationship("Image", back_populates="description_note")
    embedding = relationship(
        "DescriptionNoteEmbedding", uselist=False,
        back_populates="note", cascade="all, delete-orphan",
    )


class DescriptionNoteEmbedding(Base):
    __tablename__ = "description_note_embeddings"

    description_note_id = Column(
        UUID(as_uuid=True), ForeignKey("description_notes.image_id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding = Column(Vector(TEXT_EMBEDDING_DIM))
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_description_note_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    note = relationship("DescriptionNote", back_populates="embedding")


class DescriptionNoteLemma(Base):
    __tablename__ = "description_note_lemmas"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    lemma = Column(String, primary_key=True)
    # Populated for schema symmetry with OCRLemma, but never queried by the
    # phonetic-erratives search fallback -- a human-typed note is deliberate
    # text, same rationale as ImageTag already being excluded from that
    # fallback. See docs/superpowers/specs/2026-08-20-description-notes-design.md.
    phonetic_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_description_note_lemmas_lemma", "lemma"),
        Index(
            "ix_description_note_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )

    image = relationship("Image", back_populates="description_note_lemmas")


class DescriptionLemma(Base):
    __tablename__ = "description_lemmas"

    # Keyed by description (not image) so a rejected description's lemmas can be excluded at
    # query time via description_not_rejected(), with no batch rerun on feedback changes.
    image_description_id = Column(
        UUID(as_uuid=True), ForeignKey("image_descriptions.id", ondelete="CASCADE"), primary_key=True,
    )
    lemma = Column(String, primary_key=True)
    # Populated for schema symmetry with OCRLemma; never queried (descriptions are English
    # LLM output, the phonetic erratives fallback does not apply).
    phonetic_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_description_lemmas_lemma", "lemma"),
        Index(
            "ix_description_lemmas_lemma_trgm",
            "lemma",
            postgresql_using="gin",
            postgresql_ops={"lemma": "gin_trgm_ops"},
        ),
    )


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    searched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    query = Column(Text, nullable=True)
    client = Column(String(20), nullable=False, default="unknown")
    result_count = Column(Integer, nullable=False, default=0)
    kind = Column(String(20), nullable=False, default="search", server_default="search")
    language = Column(String(8), nullable=True)
    query_hash = Column(String(64), nullable=True)
    situation = Column(Text, nullable=True)
    degraded = Column(Boolean, nullable=True)
    fallback = Column(String(64), nullable=True)
    parse_ms = Column(Integer, nullable=True)
    retrieval_ms = Column(Integer, nullable=True)

    tags = relationship("SearchHistoryTag", back_populates="search", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_search_history_searched_at", searched_at.desc()),
        Index("ix_search_history_kind_searched_at", "kind", searched_at.desc()),
        Index("ix_search_history_query_hash", "query_hash"),
    )


class SearchHistoryTag(Base):
    __tablename__ = "search_history_tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    search_id = Column(
        UUID(as_uuid=True),
        ForeignKey("search_history.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category = Column(String, nullable=False)
    value = Column(String, nullable=False)

    search = relationship("SearchHistory", back_populates="tags")


class TrendSource(Base):
    __tablename__ = "trend_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    extraction: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="source"
    )

    def __repr__(self) -> str:
        return f"<TrendSource id={self.id} name={self.name!r} connector_type={self.connector_type!r}>"


class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"
    aborted = "aborted"

    def __str__(self) -> str:
        return self.value


class TriggerType(enum.Enum):
    manual = "manual"
    scheduled = "scheduled"
    unknown = "unknown"

    def __str__(self) -> str:
        return self.value


class BatchRun(Base):
    __tablename__ = "batch_runs"
    __table_args__ = (
        Index(
            "ix_batch_runs_one_active_per_kind", "kind",
            unique=True,
            postgresql_where=sa.text("status = 'started'"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=str(RunStatus.started),
    )
    stage: Mapped[str | None] = mapped_column(String(50), nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="run"
    )

    def __repr__(self) -> str:
        return f"<BatchRun run_id={self.run_id} kind={self.kind!r} created_at={self.created_at}>"


class TrendsRunResult(Base):
    __tablename__ = "trends_run_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("batch_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("trend_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    # relationships
    run: Mapped["BatchRun"] = relationship("BatchRun", back_populates="results")
    source: Mapped["TrendSource"] = relationship("TrendSource", back_populates="results")

    def __repr__(self) -> str:
        return (
            f"<TrendsRunResult id={self.id} label={self.label!r}"
            f" name={self.name!r} value={self.value}>"
        )


class StatisticsSnapshot(Base):
    """Latest precomputed result of an expensive statistics query, one row per `name`
    (only 'corpus' today). See docs/superpowers/specs/2026-09-29-precomputed-statistics-design.md."""
    __tablename__ = "statistics_snapshots"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)


class MemeProfile(Base):
    """What a meme conveys and when it is usable as a reply (epic meme-from-text, P1 spec decision 7).
    One row per image. Staleness is derived, never stored: taxonomy_version, extractor_prompt_version,
    extractor_model or inputs_fingerprint differs from what a fresh extraction would use."""
    __tablename__ = "meme_profiles"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True)
    taxonomy_version = Column(Integer, nullable=False)
    extractor_model = Column(String, nullable=False)
    extractor_prompt_version = Column(String, nullable=False)
    inputs_fingerprint = Column(String(64), nullable=False)
    situation_text = Column(Text, nullable=True)
    situation_confidence = Column(Float, nullable=True)
    use_when = Column(Text, nullable=True)
    human_verified = Column(Boolean, nullable=False, server_default=sa.false(), default=False)
    extracted_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())


class MemeProfileValue(Base):
    """One controlled-vocabulary value of one axis for one meme. value_key is validated in code against
    the YAML taxonomy (no foreign key: the vocabulary is not in the database)."""
    __tablename__ = "meme_profile_values"

    image_id = Column(UUID(as_uuid=True), ForeignKey("meme_profiles.image_id", ondelete="CASCADE"), primary_key=True)
    axis = Column(String(16), primary_key=True)
    value_key = Column(String(48), primary_key=True)
    confidence = Column(Float, nullable=False)
    source = Column(String(8), nullable=False)

    __table_args__ = (
        Index("ix_meme_profile_values_axis_value_key", "axis", "value_key"),
        CheckConstraint("axis IN ('emotion', 'stance', 'topic', 'tone')", name="ck_meme_profile_values_axis"),
        CheckConstraint("source IN ('model', 'human')", name="ck_meme_profile_values_source"),
    )


class MemeProfileSituationEmbedding(Base):
    """Embedding of MemeProfile.situation_text from the Ollama-served model chosen in P1. model_name and
    model_digest make a model change a re-embed. Independent of the sentence-transformers vectors."""
    __tablename__ = "meme_profile_situation_embeddings"

    image_id = Column(UUID(as_uuid=True), ForeignKey("meme_profiles.image_id", ondelete="CASCADE"), primary_key=True)
    embedding = Column(Vector(PROFILE_EMBEDDING_DIM))
    model_name = Column(String, nullable=False)
    model_digest = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_meme_profile_situation_embeddings_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class MemeTextRating(Base):
    """A rating the owner gave to one result of a meme-from-text search (epic meme-from-text, P5b).
    query_hash is sha256 of the message normalised like the parser (NFC, collapsed whitespace, cut at
    parse.max_input_chars); a change of that limit changes new hashes, old ratings keep their old hash.
    query_text is stored only because a rating exists and can be cleared per message (forget text)."""
    __tablename__ = "meme_text_ratings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_hash = Column(String(64), nullable=False)
    query_text = Column(Text, nullable=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), nullable=False)
    grade = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False)
    reading = Column(String(8), nullable=False)
    parser_version = Column(String, nullable=False)
    taxonomy_version = Column(Integer, nullable=False)
    degraded = Column(Boolean, nullable=False, server_default=sa.false(), default=False)
    fallback = Column(String, nullable=False, server_default="none", default="none")
    language = Column(String(8), nullable=False)
    rated_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("query_hash", "image_id", name="uq_meme_text_ratings_query_image"),
        Index("ix_meme_text_ratings_rated_at", "rated_at"),
        CheckConstraint("grade IN (0, 1, 2)", name="ck_meme_text_ratings_grade"),
        CheckConstraint("rank >= 1", name="ck_meme_text_ratings_rank"),
        CheckConstraint("reading IN ('parsed', 'edited')", name="ck_meme_text_ratings_reading"),
        {"comment": (
            "query_hash is sha256 of the message normalised like the parser (NFC, collapsed whitespace, cut at "
            "parse.max_input_chars); query_text is stored only because a rating exists and can be cleared per message"
        )},
    )
