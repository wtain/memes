import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

load_dotenv()

images_dir = os.getenv('BASE_PATH')
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


incoming_path = os.getenv('INCOMING_PATH')
if incoming_path:
    INCOMING_DIR = Path(incoming_path)
else:
    INCOMING_DIR = IMAGES_DIR / "incoming"
