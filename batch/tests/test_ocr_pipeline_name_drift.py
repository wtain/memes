"""The backend image has no batch/, so diagnostics_repository duplicates the OCR pipeline marker; keep both in step."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")

from Backend.app.repositories import diagnostics_repository
from batch import extract_text_from_memes


def test_diagnostics_ocr_pipeline_matches_batch_pipeline():
    assert diagnostics_repository.OCR_PIPELINE == extract_text_from_memes.PIPELINE
