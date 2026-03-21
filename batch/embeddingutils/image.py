import os

import numpy as np
import torch
from PIL import Image


def load_image(path):
    image = Image.open(path).convert("RGB")
    return image


def embed_image(image, device, model, preprocess) -> np.ndarray:
    image_input = preprocess(image).unsqueeze(0).to(device)

    with torch.no_grad():
        features = model.encode_image(image_input)

    features = features / features.norm(dim=-1, keepdim=True)

    return features.cpu().numpy()[0]