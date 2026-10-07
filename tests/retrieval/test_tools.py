import pytest

from retrieval import tools
from retrieval.model import Filters
from retrieval.pipeline import MemeFromTextPipeline
from taxonomy import load
from tests.retrieval.helpers import make_candidate, make_config, make_similarity
from tests.retrieval.test_pipeline import FakeEmbedder, FakeParser, FakeRepository

TAX = load()


def _pipeline():
    return MemeFromTextPipeline(make_config(), FakeParser(), FakeEmbedder(), make_similarity(), TAX)


@pytest.mark.asyncio
async def test_search_tool_returns_the_output_shape():
    candidates = [make_candidate("a", cosine=0.85, values={"emotion": (("joy", 0.9),)}, cluster_id=3)]

    output = await tools.search_memes_from_text(
        {"text": "My friend got promoted!", "limit": 5, "filters": {"emotion": ["joy"], "tags": [{"key": "c", "value": "v"}]}},
        _pipeline(), FakeRepository(candidates))

    assert set(output) == {"items", "facets", "nextCursor", "hasNext", "profile", "degraded", "fallback", "meta"}
    item = output["items"][0]
    assert set(item) == {"imageId", "score", "axisPath", "explanation", "clusterId", "clusterSize"}
    assert item["imageId"] == "a" and item["axisPath"] is True and item["clusterId"] == 3 and item["clusterSize"] == 1
    assert {f["name"] for f in output["facets"]} == {"emotion", "stance", "topic", "tone"}
    assert output["facets"][0]["buckets"] == [{"value": "joy", "count": 1}]
    assert output["hasNext"] is False and output["nextCursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [None, "text", {}, {"text": 5}, {"text": "x", "limit": "many"},
                                       {"text": "x", "languageHint": "fr"}, {"text": "x", "filters": {"emotion": "joy"}},
                                       {"text": "x", "filters": {"tags": 5}}])
async def test_a_malformed_call_raises_value_error(arguments):
    with pytest.raises(ValueError):
        await tools.search_memes_from_text(arguments, _pipeline(), FakeRepository([]))


def test_schema_dictionaries_describe_the_calls():
    assert tools.SEARCH_INPUT_SCHEMA["required"] == ["text"]
    assert tools.SEARCH_OUTPUT_SCHEMA["required"] == ["items", "hasNext", "profile", "degraded", "fallback", "meta"]
    assert tools.SCORE_INPUT_SCHEMA["required"] == ["candidates", "query"]


def test_score_candidates_tool_scores_plain_dicts():
    config, similarity = make_config(), make_similarity()
    arguments = {
        "query": {"situation": "x", "language": "en", "degraded": False,
                  "emotion": [{"key": "anger", "confidence": 0.9}], "stance": [], "topic": [], "tone": []},
        "candidates": [{"imageId": "a", "cosine": 0.85, "values": {"emotion": [["annoyance", 0.6]]}, "lexical": 0.0,
                        "axisPath": True, "clusterId": None, "situationText": None, "useWhen": None}],
        "lexicalActive": False,
    }

    scored = tools.score_candidates(arguments, config, similarity)

    assert scored[0]["imageId"] == "a" and 0 < scored[0]["score"] <= 1
    emotion = next(a for a in scored[0]["explanation"]["axes"] if a["axis"] == "emotion")
    assert emotion["matched"][0]["memeKey"] == "annoyance"
    assert Filters().is_empty()
