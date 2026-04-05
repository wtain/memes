from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
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

    embedding = Column(Vector(EMBEDDING_DIM))

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="embeddings")


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
