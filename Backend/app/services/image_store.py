import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

# IMAGES_DIR = Path("app/data/images")
images_dir = os.getenv('IMAGES_DIR')
IMAGES_DIR = Path(images_dir)


def list_images() -> List[str]:
    if not IMAGES_DIR.exists():
        return []

    return [
        p.name
        for p in IMAGES_DIR.iterdir()
        if p.is_file()
    ]


def image_exists(image_id: str) -> bool:
    return (IMAGES_DIR / image_id).exists()


def get_image_path(image_id: str) -> Path:
    return IMAGES_DIR / image_id
