
from PIL import Image


def load_image(path):
    image = Image.open(path).convert("RGB")
    return image
