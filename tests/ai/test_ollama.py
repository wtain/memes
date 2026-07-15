import ollama

from ai.ollama import OllamaImageDescriber, OllamaConceptNamer, build_concept_naming_prompt


def test_describe_passes_prompt_model_and_num_ctx_through(monkeypatch):
    def fake_chat(model, messages, options):
        assert model == "qwen2.5vl:7b"
        assert messages[0]["content"] == "Explain the joke."
        assert messages[0]["images"] == ["/path/to/image.jpg"]
        assert options == {"num_ctx": 8192}
        return {"message": {"content": "a description"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    describer = OllamaImageDescriber()

    result = describer.describe("/path/to/image.jpg", "Explain the joke.", "qwen2.5vl:7b", 8192)

    assert result == "a description"


def test_build_concept_naming_prompt_formats_words_and_frequencies():
    system, user = build_concept_naming_prompt("ru", [("мем", 232), ("мемасик", 45)])

    assert "concept name" in system.lower()
    assert "Language: Russian" in user
    assert "мем (232)" in user
    assert "мемасик (45)" in user


def test_build_concept_naming_prompt_unknown_language_falls_back_to_code():
    _, user = build_concept_naming_prompt("fr", [("chat", 10)])

    assert "Language: fr" in user


def test_name_cluster_returns_stripped_content(monkeypatch):
    def fake_chat(model, messages):
        assert model == "qwen2"
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        return {"message": {"content": " meme \n"}}

    monkeypatch.setattr(ollama, "chat", fake_chat)
    namer = OllamaConceptNamer(model="qwen2")

    result = namer.name_cluster("ru", [("мем", 232)])

    assert result == "meme"


def test_name_cluster_returns_none_on_failure(monkeypatch):
    def fake_chat(model, messages):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(ollama, "chat", fake_chat)
    namer = OllamaConceptNamer(model="qwen2")

    result = namer.name_cluster("ru", [("мем", 232)])

    assert result is None
