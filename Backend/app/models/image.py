from pydantic import BaseModel
from typing import List


class ImageSummary(BaseModel):
    id: str
    preview_url: str


class ImageDetail(BaseModel):
    id: str
    content_url: str
    preview_url: str
    categories: List[str]
    ocr_text: str | None = None
