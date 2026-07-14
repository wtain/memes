import pytest

from ai.image_description_prompts import PromptConfig, load_prompts, resolve_model


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_load_prompts_parses_all_fields(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "- key: general_description\n"
        "  prompt: \"What is shown in this image?\"\n"
        "- key: humor_explanation\n"
        "  prompt: \"Explain the joke, if any.\"\n"
        "  model: llava\n"
    )

    prompts = load_prompts(str(path))

    assert prompts == [
        PromptConfig(key="general_description", prompt="What is shown in this image?", model=None),
        PromptConfig(key="humor_explanation", prompt="Explain the joke, if any.", model="llava"),
    ]


def test_load_prompts_raises_on_duplicate_key(tmp_path):
    path = tmp_path / "prompts.yaml"
    path.write_text(
        "- key: dup\n"
        "  prompt: \"first\"\n"
        "- key: dup\n"
        "  prompt: \"second\"\n"
    )

    with pytest.raises(ValueError, match="dup"):
        load_prompts(str(path))


def test_resolve_model_uses_prompt_override_when_present():
    prompt = PromptConfig(key="k", prompt="p", model="qwen2.5vl:7b")
    settings = _FakeSettings({"image_descriptions.model": "llava"})

    assert resolve_model(prompt, settings) == "qwen2.5vl:7b"


def test_resolve_model_falls_back_to_global_default():
    prompt = PromptConfig(key="k", prompt="p", model=None)
    settings = _FakeSettings({"image_descriptions.model": "llava"})

    assert resolve_model(prompt, settings) == "llava"
