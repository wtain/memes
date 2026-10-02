import json
from dataclasses import dataclass

import ollama

from ai.meme_profile_prompt import CONFIDENCE_LEVELS, RETRY_TEMPLATE, build_messages, build_response_schema
from taxonomy.loader import load
from taxonomy.model import Taxonomy
from taxonomy.validate import validate_profile

MAX_USE_WHEN_CHARS = 300


class ProfileExtractionError(Exception):
    """The model's answer was unusable even after one retry."""


@dataclass(frozen=True)
class ExtractionResult:
    profile: dict          # taxonomy.validate_profile(...).profile
    use_when: str | None


class OllamaProfileExtractor:
    """One schema-constrained Ollama call per image (P2 spec, decision 5). Synchronous: callers run it
    through asyncio.to_thread, as build_image_descriptions does for OllamaImageDescriber."""

    def __init__(self, tax: Taxonomy | None = None, *, seed: int, num_ctx: int):
        self._tax = tax or load()
        self._seed = seed
        self._num_ctx = num_ctx
        self._schema = build_response_schema(self._tax)

    def extract(self, *, model: str, note: str | None, descriptions: list[tuple[str, str]],
                ocr: str | None, image_path: str | None = None) -> ExtractionResult:
        messages = build_messages(note=note, descriptions=descriptions, ocr=ocr, tax=self._tax,
                                  has_image=image_path is not None)
        if image_path is not None:
            messages[-1]["images"] = [image_path]

        last_error = "no answer"
        raw = ""
        for attempt in range(2):
            if attempt == 1:
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": RETRY_TEMPLATE.format(error=last_error)},
                ]
            response = ollama.chat(
                model=model, messages=messages, format=self._schema,
                options={"temperature": 0, "seed": self._seed, "num_ctx": self._num_ctx},
            )
            raw = response["message"]["content"]
            try:
                return self._parse(raw)
            except ProfileExtractionError as error:
                last_error = str(error)
        raise ProfileExtractionError(last_error)

    def _parse(self, raw: str) -> ExtractionResult:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            raise ProfileExtractionError("the answer is not valid JSON") from None
        if not isinstance(data, dict):
            raise ProfileExtractionError("the answer is not a JSON object")

        profile_input: dict = {"situation": data.get("situation")}
        for axis in self._tax.controlled_axes():
            items = data.get(axis, [])
            if not isinstance(items, list):
                raise ProfileExtractionError(f"{axis} must be a list")
            converted = []
            for item in items:
                level = item.get("confidence") if isinstance(item, dict) else None
                if level not in CONFIDENCE_LEVELS:
                    raise ProfileExtractionError(f"{axis}: confidence must be one of {list(CONFIDENCE_LEVELS)}")
                converted.append({"key": item.get("key"), "confidence": CONFIDENCE_LEVELS[level], "source": "model"})
            profile_input[axis] = converted

        validated = validate_profile(profile_input, self._tax)
        if not validated.ok:
            raise ProfileExtractionError("; ".join(validated.errors))

        use_when = data.get("useWhen")
        if use_when is not None and not isinstance(use_when, str):
            raise ProfileExtractionError("useWhen must be a string")
        use_when = (use_when or "").strip() or None
        if use_when is not None and len(use_when) > MAX_USE_WHEN_CHARS:
            raise ProfileExtractionError(f"useWhen is longer than {MAX_USE_WHEN_CHARS} characters")
        return ExtractionResult(profile=validated.profile, use_when=use_when)
