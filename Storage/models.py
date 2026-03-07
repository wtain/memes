from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column, String, Integer, Float, Text, ForeignKey,
    DateTime, JSON, func, Numeric, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
import uuid


Base = declarative_base()


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
    metrics = relationship("ImageMetrics", uselist=False, back_populates="image")
    errors = relationship("ProcessingError", back_populates="image")
    embeddings = relationship("Embedding", back_populates="image", cascade="all, delete-orphan")


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


class Embedding(Base):
    __tablename__ = "embeddings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    image_id = Column(UUID(as_uuid=True), ForeignKey("images.id", ondelete="CASCADE"), index=True)

    embedding = Column(Vector(512))

    created_at = Column(DateTime, server_default=func.now())

    image = relationship("Image", back_populates="embeddings")


class Concept(Base):
    __tablename__ = "concepts"

    id = Column(Integer, primary_key=True)
    name = Column(String)
    embedding = Column(Vector(512))


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
