from transformers import CLIPProcessor, CLIPModel
from PIL import Image
import torch

print("CUDA available:", torch.cuda.is_available())
print("CUDA device count:", torch.cuda.device_count())
print("Current device:", torch.cuda.current_device() if torch.cuda.is_available() else None)
print("Device name:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)


model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32",
    use_safetensors=True)
processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")

image = Image.open("c:\\Users\\ramiz\\OneDrive\\Pictures\\Samsung Gallery\\DCIM\\MetalMemes\\FB_IMG_1735080991610.jpg")

inputs = processor(
    text=[
        "Death metal band logo",
        "Xavleg logo",
        "Saint Anger",
        "James Hetfield from Metallica",
        "Lars Ulrich from Metallica",
        "cat",
        "dog",
        "girl",
    ],
    images=image,
    return_tensors="pt",
    padding=True
)

with torch.no_grad():
    outputs = model(**inputs)
    logits = outputs.logits_per_image
    probs = logits.softmax(dim=1)
    scores = logits.squeeze(0)  # raw similarities

print(probs)
print(scores)