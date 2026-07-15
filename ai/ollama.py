import ollama


class OllamaImageDescriber:

    def describe(self, path: str, prompt: str, model: str, num_ctx: int) -> str:
        response = ollama.chat(
            model=model,
            messages=[{
                'role': 'user',
                'content': prompt,
                'images': [path]
            }],
            options={'num_ctx': num_ctx}
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

_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
    "es": "Spanish",
}


def build_concept_naming_prompt(language: str, word_freqs: list[tuple[str, int]]) -> tuple[str, str]:
    system = (
        "You are a concise tagger for a meme image database. Given a list of word forms "
        "and their frequency of occurrence in image texts, propose a single short concept "
        "name (1-3 English words) that best describes what they have in common. "
        "Respond with the concept name only, no explanation."
    )
    language_name = _LANGUAGE_NAMES.get(language, language)
    word_lines = "\n".join(f"- {word} ({freq})" for word, freq in word_freqs)
    user = f"Language: {language_name}\nWords and frequencies:\n{word_lines}"
    return system, user


class OllamaConceptNamer:

    def __init__(self, model: str = "qwen2"):
        self.model = model

    def name_cluster(self, language: str, word_freqs: list[tuple[str, int]]) -> str | None:
        system, user = build_concept_naming_prompt(language, word_freqs)
        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
            return response["message"]["content"].strip()
        except Exception as e:
            print(f"Ollama naming failed for cluster ({language}): {e}")
            return None