from dataclasses import dataclass

import yaml


@dataclass
class PromptConfig:
    key: str
    prompt: str
    model: str | None = None


def load_prompts(path: str) -> list[PromptConfig]:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    prompts = [PromptConfig(**entry) for entry in raw]

    seen_keys = set()
    for prompt in prompts:
        if prompt.key in seen_keys:
            raise ValueError(f"Duplicate prompt key in {path}: {prompt.key!r}")
        seen_keys.add(prompt.key)

    return prompts


def resolve_model(prompt: PromptConfig, settings) -> str:
    return prompt.model or settings.get("image_descriptions.model")
