import os

UNSUPPORTED_IMAGE_EXTENSIONS = {"webp", "gif", "svg", "avif"}
# Extensions the vision models in this pipeline (Ollama's llava, ultralytics YOLO)
# can't reliably decode -- confirmed via real "Failed to load image or audio
# file" Ollama failures, 2026-08-02. This is an extension check, not content
# sniffing: a file honestly labeled .gif/.svg/.avif is caught, but a mislabeled
# file (e.g. webp content saved with a .jpg extension, a known issue in this
# corpus -- see CLAUDE.md) is not, since the extension itself lies.


def has_unsupported_image_extension(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext in UNSUPPORTED_IMAGE_EXTENSIONS
