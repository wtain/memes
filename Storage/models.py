from datetime import datetime
import enum
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index, Boolean,
    BigInteger, UniqueConstraint, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
import uuid


Base = declarative_base()

EMBEDDING_DIM = 512

TEXT_EMBEDDING_DIM = 1024


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


class ImageMetrics(Base):
    __tablename__ = "image_metrics"

    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), primary_key=True, index=True)

    read_time_ms = Column(Numeric)
    preprocess_time_ms = Column(Numeric)
    ocr_time_ms = Column(Numeric)
    total_time_ms = Column(Numeric)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="metrics")


class OCRText(Base):
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


class ImageDescription(Base):
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


class ImageTag(Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    key = Column(String)
    value = Column(String)
    source = Column(String)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="tags")


class OCRLemma(Base):
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
    remarks = Column(Text)

    image = relationship("Image", back_populates="image_extras")


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


class SearchHistory(Base):
    __tablename__ = "search_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    searched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    query = Column(Text, nullable=True)
    client = Column(String(20), nullable=False, default="unknown")
    result_count = Column(Integer, nullable=False, default=0)

    tags = relationship("SearchHistoryTag", back_populates="search", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_search_history_searched_at", searched_at.desc()),
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