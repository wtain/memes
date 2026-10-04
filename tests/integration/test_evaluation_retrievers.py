"""
Integration tests for the evaluation baseline retrievers. Requires a live PostgreSQL instance with
pgvector -- see tests/integration/conftest.py. No Ollama: the embedder is a fake.
"""
import uuid

import numpy as np
import pytest

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.retrievers.axis_match import AxisMatchRetriever
from evaluation.retrievers.lexical import LexicalRetriever
from evaluation.retrievers.situation_embedding import SituationEmbeddingRetriever
from repository.meme_profiles import MemeProfileRepository, ProfileData, ProfileMeta
from Storage.models import PROFILE_EMBEDDING_DIM, Image, OCRLemma


def _query(text="hello", **expected):
    values = dict(situation="s", emotion=(), stance=(), topic=(), tone=())
    values.update(expected)
    return GoldQuery("q1", "en", text, "short", "easy", ExpectedProfile(**values), True)


async def _image(session, *lemmas) -> Image:
    image = Image(filename=f"{uuid.uuid4()}.jpg")
    session.add(image)
    await session.flush()
    for lemma in lemmas:
        session.add(OCRLemma(image_id=image.id, lemma=lemma))
    await session.flush()
    return image


async def _profile(session, image, *, situation="a situation", **axes):
    values = {axis: [{"key": key, "confidence": confidence, "source": "model"} for key, confidence in pairs]
              for axis, pairs in axes.items()}
    await MemeProfileRepository(session).save_profile(
        image.id, ProfileData(situation, None, None, values), ProfileMeta(2, "text-model", "p2-test", "f" * 64, False))
    await session.flush()


def _vector(*leading):
    vector = np.zeros(PROFILE_EMBEDDING_DIM, dtype=np.float32)
    vector[:len(leading)] = leading
    return vector.tolist()


class FakeEmbedder:
    def __init__(self, vectors_by_text):
        self.vectors_by_text = vectors_by_text
        self.calls = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return np.array([self.vectors_by_text[text] for text in texts], dtype=np.float32)


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_and_requires_every_word_and_is_empty_for_a_message_with_an_unmatched_word(db_session):
    both = await _image(db_session, "boyfriend", "pizza")
    one = await _image(db_session, "boyfriend")
    corpus = [str(both.id), str(one.id)]

    matching = await LexicalRetriever(db_session, "and").retrieve(_query("boyfriend pizza"), corpus, 10)
    unmatched = await LexicalRetriever(db_session, "and").retrieve(_query("boyfriend zzzqqq"), corpus, 10)

    assert [image_id for image_id, _ in matching] == [str(both.id)]
    assert unmatched == []
    assert LexicalRetriever(db_session, "and").name == "lexical_and"


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_or_scores_any_token_and_ranks_more_matches_higher(db_session):
    best = await _image(db_session, "boyfriend", "pizza")
    partial = await _image(db_session, "pizza")
    nothing = await _image(db_session, "weather")
    corpus = [str(best.id), str(partial.id), str(nothing.id)]

    result = await LexicalRetriever(db_session, "or").retrieve(_query("boyfriend pizza tonight"), corpus, 10)

    ids = [image_id for image_id, _ in result]
    assert ids == [str(best.id), str(partial.id)]
    assert result[0][1] > result[1][1] > 0


@pytest.mark.asyncio(loop_scope="session")
async def test_lexical_results_are_restricted_to_the_corpus(db_session):
    inside = await _image(db_session, "pizza")
    outside = await _image(db_session, "pizza")

    result = await LexicalRetriever(db_session, "or").retrieve(_query("pizza"), [str(inside.id)], 10)

    assert [image_id for image_id, _ in result] == [str(inside.id)]
    assert str(outside.id) not in [image_id for image_id, _ in result]


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_match_ranks_by_agreement_weighted_by_confidence_and_skips_non_matches(db_session):
    strong = await _image(db_session)
    weak = await _image(db_session)
    unrelated = await _image(db_session)
    await _profile(db_session, strong, emotion=[("anger", 0.9)], stance=[("mock", 0.9)])
    await _profile(db_session, weak, emotion=[("anger", 0.3)], stance=[("refuse", 0.9)])
    await _profile(db_session, unrelated, emotion=[("joy", 0.9)], stance=[("agree", 0.9)])
    corpus = [str(strong.id), str(weak.id), str(unrelated.id)]

    result = await AxisMatchRetriever(db_session).retrieve(_query(emotion=("anger",), stance=("mock",)), corpus, 10)

    assert [image_id for image_id, _ in result] == [str(strong.id), str(weak.id)]
    assert result[0][1] == pytest.approx((0.9 + 0.9) / 2)
    assert result[1][1] == pytest.approx((0.3 + 0.0) / 2)


@pytest.mark.asyncio(loop_scope="session")
async def test_axis_match_with_no_expected_values_returns_nothing(db_session):
    image = await _image(db_session)
    await _profile(db_session, image, emotion=[("anger", 0.9)])

    assert await AxisMatchRetriever(db_session).retrieve(_query(), [str(image.id)], 10) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_embedding_ranks_by_cosine_within_the_corpus_and_the_configured_model(db_session):
    near = await _image(db_session)
    far = await _image(db_session)
    outside = await _image(db_session)
    other_model = await _image(db_session)
    repo = MemeProfileRepository(db_session)
    for image in (near, far, outside, other_model):
        await _profile(db_session, image)
    await repo.save_situation_embedding(near.id, _vector(1.0, 0.1), "bge-large", "d1")
    await repo.save_situation_embedding(far.id, _vector(0.0, 1.0), "bge-large", "d1")
    await repo.save_situation_embedding(outside.id, _vector(1.0, 0.0), "bge-large", "d1")
    await repo.save_situation_embedding(other_model.id, _vector(1.0, 0.0), "another-model", "d9")
    await db_session.flush()
    embedder = FakeEmbedder({"my boss moved the deadline": _vector(1.0, 0.0)})
    retriever = SituationEmbeddingRetriever(db_session, embedder, "bge-large")
    query = _query("my boss moved the deadline")

    await retriever.prepare([query])
    result = await retriever.retrieve(query, [str(near.id), str(far.id), str(other_model.id)], 10)

    assert [image_id for image_id, _ in result] == [str(near.id), str(far.id)]
    assert result[0][1] > result[1][1]
    assert embedder.calls == [["my boss moved the deadline"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_situation_embedding_without_prepare_embeds_on_demand(db_session):
    image = await _image(db_session)
    await _profile(db_session, image)
    await MemeProfileRepository(db_session).save_situation_embedding(image.id, _vector(1.0), "bge-large", "d1")
    await db_session.flush()
    retriever = SituationEmbeddingRetriever(db_session, FakeEmbedder({"hello": _vector(1.0)}), "bge-large")

    result = await retriever.retrieve(_query("hello"), [str(image.id)], 10)

    assert [image_id for image_id, _ in result] == [str(image.id)]
