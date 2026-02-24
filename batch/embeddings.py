import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor


class EmbeddingsDetector:

    def __init__(self, tokens):
        print("CUDA available:", torch.cuda.is_available())
        print("CUDA device count:", torch.cuda.device_count())
        print("Current device:", torch.cuda.current_device() if torch.cuda.is_available() else None)
        print("Device name:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
        self.model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32",
                                          use_safetensors=True)
        self.processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        self.tokens = tokens

    def detect_embeddings(self, path):
        image = Image.open(path)

        inputs = self.processor(
            text=self.tokens,
            images=image,
            return_tensors="pt",
            padding=True
        )

        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits_per_image
            # probs = logits.softmax(dim=1)
            scores = logits.squeeze(0)  # raw similarities

        # return {t: (p, s) for t, p, s in zip(self.tokens, probs, scores)}
        return {t: s.item() for t, s in zip(self.tokens, scores)}