import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

_images_dir = os.getenv('BASE_PATH')
if not _images_dir:
    raise RuntimeError("BASE_PATH environment variable is required but not set")
_IMAGES_DIR = Path(_images_dir)

_incoming_raw = os.getenv('INCOMING_PATH')
_INCOMING_DIR = Path(_incoming_raw) if _incoming_raw else _IMAGES_DIR / "incoming"


def get_image_path(filename: str) -> Path:
    return _IMAGES_DIR / filename


def list_images() -> List[str]:
    if not _IMAGES_DIR.exists():
        return []
    return [p.name for p in _IMAGES_DIR.iterdir() if p.is_file()]


def image_exists(filename: str) -> bool:
    return (_IMAGES_DIR / filename).exists()


def save_incoming(filename: str, data: bytes) -> None:
    _INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    (_INCOMING_DIR / filename).write_bytes(data)