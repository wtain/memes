from batch.draft_concepts_from_clusters import (
    collect_declared_tags,
    collect_existing_keys,
    collect_existing_words,
    resolve_key,
    slugify,
    top_lemma,
)


def test_slugify_quoted_multiword_name():
    assert slugify('"Local Dialect Words"') == "local_dialect_words"


def test_slugify_single_word():
    assert slugify("Memes") == "memes"


def test_slugify_cyrillic_name():
    assert slugify("Сон") == "сон"


def test_slugify_none_returns_empty():
    assert slugify(None) == ""


def test_slugify_empty_string_returns_empty():
    assert slugify("") == ""


def test_slugify_collapses_punctuation():
    assert slugify("Purchase / Activities!!") == "purchase_activities"


def test_top_lemma_returns_first_member():
    cluster = {"members": {"спать": 640, "сон": 100}}
    assert top_lemma(cluster) == "спать"


def test_collect_existing_words_includes_words_and_fuzzy():
    concepts_data = {
        "sleep": {"words": ["спать", "сон"]},
        "salary": {"words": ["зарплата"], "fuzzy": [{"word": "зорплата", "threshold": 85}]},
    }
    result = collect_existing_words(concepts_data)
    assert result == {"спать", "сон", "зарплата", "зорплата"}


def test_collect_existing_words_handles_missing_words_key():
    assert collect_existing_words({"empty": {}}) == set()


def test_collect_existing_words_handles_none():
    assert collect_existing_words(None) == set()


def test_collect_existing_keys():
    assert collect_existing_keys({"sleep": {}, "family": {}}) == {"sleep", "family"}


def test_collect_existing_keys_handles_none():
    assert collect_existing_keys(None) == set()


def test_collect_declared_tags():
    tags_data = {"defaults": {"threshold": 1.0}, "tags": {"тема:сон": {}, "тема:семья": {}}}
    assert collect_declared_tags(tags_data) == {"тема:сон", "тема:семья"}


def test_resolve_key_uses_slugified_ollama_name():
    assert resolve_key("Sleeping", "спать", existing_keys=set()) == "sleeping"


def test_resolve_key_falls_back_to_lemma_when_no_ollama_name():
    assert resolve_key(None, "спать", existing_keys=set()) == "спать"


def test_resolve_key_disambiguates_collision():
    assert resolve_key("Sleeping", "спать", existing_keys={"sleeping"}) == "sleeping_2"


def test_resolve_key_disambiguates_multiple_collisions():
    key = resolve_key("Sleeping", "спать", existing_keys={"sleeping", "sleeping_2"})
    assert key == "sleeping_3"