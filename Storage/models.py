from datetime import datetime
import enum
from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index, Boolean,
    BigInteger
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
import uuid


Base = declarative_base()

EMBEDDING_DIM = 512


class Image(Base):
    __tablename__ = "images"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename = Column(String, nullable=False, index=True)
    content_hash = Column(String, nullable=True, index=True)
    width = Column(Integer)
    height = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index(
            "ix_images_created_at_id_desc",
            created_at.desc(),
            id.desc()
        ),
    )

    texts = relationship("OCRText", back_populates="image", cascade="all, delete-orphan")
    descriptions = relationship("OllamaDescription", back_populates="image", cascade="all, delete-orphan")
    metrics = relationship("ImageMetrics", uselist=False, back_populates="image")
    errors = relationship("ProcessingError", back_populates="image")
    embeddings = relationship("Embedding", back_populates="image", cascade="all, delete-orphan")
    tags = relationship("ImageTag", back_populates="image", cascade="all, delete-orphan")
    image_extras = relationship("ImageExtras", back_populates="image", cascade="all, delete-orphan")


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

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="texts")


class OllamaDescription(Base):
    __tablename__ = "ollama_description"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    text = Column(Text, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="descriptions")


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    embedding = Column(Vector(EMBEDDING_DIM), index=True)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="embeddings")

"""
DROP TABLE IF EXISTS tmp_duplicates;

CREATE TABLE tmp_duplicates AS
WITH image_embeddings AS (
    SELECT i.id, e.embedding, i.created_at
    FROM images i
    JOIN embeddings e ON i.id = e.image_id
)
SELECT ROW_NUMBER() OVER () AS id,
       ie1.id AS image_id1,
       ie2.id AS image_id2,
       ie1.created_at as created_at,
       ie1.embedding <=> ie2.embedding AS distance
FROM image_embeddings ie1
JOIN image_embeddings ie2 ON true;

-- Add PK constraint after the fact (CTAS doesn't support inline constraints)
ALTER TABLE tmp_duplicates ADD PRIMARY KEY (id);

-- Covers the WHERE + ORDER BY (most important)
CREATE INDEX idx_tmp_duplicates_id_distance ON tmp_duplicates (id DESC, distance);

-- Or if distance filtering is very selective, separate indexes may work too:
CREATE INDEX idx_tmp_duplicates_distance ON tmp_duplicates (distance);
CREATE INDEX idx_tmp_duplicates_id ON tmp_duplicates (id DESC);
"""
class TmpDuplicates(Base):
    __tablename__ = "tmp_duplicates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id1 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)
    image_id2 = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    distance = Column(Float, nullable=False)

    created_at = Column(DateTime, server_default=func.now())


class TmpImageClusters(Base):
    __tablename__ = "tmp_clusters"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)



class ImageTag(Base):
    __tablename__ = "tags"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    key = Column(String)
    value = Column(String)
    source = Column(String)

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="tags")


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

    exclude = Column(Boolean)
    remarks = Column(Text)

    image = relationship("Image", back_populates="image_extras")


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


class FeedSource(Base):
    __tablename__ = "feed_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    selector: Mapped[str] = mapped_column(Text, nullable=False)

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="source"
    )

    def __repr__(self) -> str:
        return f"<FeedSource id={self.id} name={self.name!r}>"


class RunStatus(enum.Enum):
    started = "started"
    completed = "completed"
    failed = "failed"

    def __str__(self) -> str:
        return self.value


class TrendsRun(Base):
    __tablename__ = "trends_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # status: Mapped[RunStatus] = mapped_column(
    status: Mapped[str] = mapped_column(
        # Enum(RunStatus, values_callable=lambda e: [m.value for m in e]),
        String(20),
        nullable=False,
        # default=RunStatus.started,
        default=str(RunStatus.started),
    )

    # back-ref to results
    results: Mapped[list["TrendsRunResult"]] = relationship(
        "TrendsRunResult", back_populates="run"
    )

    def __repr__(self) -> str:
        return f"<TrendsRun run_id={self.run_id} created_at={self.created_at}>"


class TrendsRunResult(Base):
    __tablename__ = "trends_run_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trends_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("feed_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    # relationships
    run: Mapped["TrendsRun"] = relationship("TrendsRun", back_populates="results")
    source: Mapped["FeedSource"] = relationship("FeedSource", back_populates="results")

    def __repr__(self) -> str:
        return (
            f"<TrendsRunResult id={self.id} label={self.label!r}"
            f" name={self.name!r} value={self.value}>"
        )