import numpy as np
import open_clip
import torch


class ClipModel:

    def __init__(self, model_name = "ViT-B-32", pretrained = "openai"):
        self.model, self.preprocess, _ = open_clip.create_model_and_transforms(
            model_name,
            pretrained=pretrained
        )

        self.tokenizer = open_clip.get_tokenizer(model_name) # not always needed! lazy?

        self.device = "cuda" if torch.cuda.is_available() else "cpu"

        self.model = self.model.to(self.device)
        self.model.eval()

    def embed_text(self, text: str):
        tokens = self.tokenizer([text]).to(self.device)

        with torch.no_grad():
            features = self.model.encode_text(tokens)

        features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy()[0]

    def embed_image(self, image) -> np.ndarray:
        image_input = self.preprocess(image).unsqueeze(0).to(self.device)

        with torch.no_grad():
            features = self.model.encode_image(image_input)

        features = features / features.norm(dim=-1, keepdim=True)

        return features.cpu().numpy()[0]