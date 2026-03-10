import sys
from pathlib import Path

# Add parent to path
project_root = Path(__file__).parent.parent.parent  # Go up 4 levels
sys.path.insert(0, str(project_root / "Storage"))

try:
    # Import assuming models are in root/models.py or root/models/
    from models import (Base,
                        Image,
                        ImageMetrics,
                        OCRText,
                        ImageTag,
                        Concept,
                        ProcessingError,
                        ImageProcessingStatus,
                        Embedding)
    from db import AsyncSessionLocal, SessionLocal, init_db
except ImportError as e:
    print(e)
    print(project_root)
    raise


__all__ = [
    'Base',
    'Image',
    'ImageMetrics',
    'OCRText',
    'ImageTag',
    'Concept',
    'Embedding',
    'ProcessingError',
    'ImageProcessingStatus',
    'AsyncSessionLocal',
    'SessionLocal',
    'init_db'
]
