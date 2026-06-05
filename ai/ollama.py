import ollama


class OllamaImageDescriber:

    def describe(self, path: str) -> str:
        response = ollama.chat(
            model='llava',
            messages=[{
                'role': 'user',
                'content': 'What is shown in this image?',
                'images': [path]
            }]
        )
        return response['message']['content']


class OllamaAnimalDetector:

    def __init__(self):
        pass

    def detect_animals(self, path):
        response = ollama.chat(
            model='llava',
            messages=[{
                'role': 'user',
                'content':
                    """
                    Which animals, celebrities and objects present in the picture?
                    Result in JSON format - object with fields 
                    "animals" (value - array of animals, empty if none), 
                    "celebrities" (value - array of celebrities, empty if none),
                    "objects" (value - array of objects, empty if none):
                    {
                    "animals": [],
                    "celebrities": [],
                    "objects": []
                    }
                    """,
                'images': [path]
            }]
        )

        response_structure = response['message']['content']
        return response_structure