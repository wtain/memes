from ultralytics import YOLO
from PIL import Image

ANIMALS = {
    "cat", "dog", "wolf", "frog", "toad", "parrot", "giraffe", "horse",
    "mouse", "rat", "bat", "bird", "bear", "zebra", "panda", "fish",
    "lion", "tiger", "owl", "penguin", "gopher", "cow", "goat", "duck", "goose",
    "donkey", "chicken", "rooster", "squid", "elephant", "sheep", "monkey", "squirrel",
    "rhino",
}


class YoloAnimalDetector:

    def __init__(self):
        # Load model
        self.model = YOLO("yolov8n.pt")  # lightweight

    def detect_animals(self, path):
        # Run inference
        results = self.model(path)

        animals = set()
        objects = set()
        for result in results:
            for box in result.boxes:
                cls_id = int(box.cls[0])
                label = self.model.names[cls_id]

                if label in ANIMALS:
                    animals.add(label)
                else:
                    objects.add(label)

        return { "animals": list(animals), "objects": list(objects) }