"""The note-lemma normalization is one function shared by the batch job and PUT. No DB needed, but
lives in the integration root because it imports repository code that needs DATABASE_URL set."""
from repository.description_note_lemmas import compute_note_lemmas, note_lemma_set
from rules.normalize import make_morph, normalize


def test_note_lemma_set_matches_the_batch_jobs_original_normalize_call():
    morph = make_morph()
    text = "A cat wearing a hat 2024"

    expected = normalize(text, morph, min_length=3, language=None, keep_digit_tokens=True)

    assert note_lemma_set(text, morph, 3) == expected
    # digit tokens are kept, but still subject to min_length (so "42" would be dropped at 3)
    assert "2024" in note_lemma_set(text, morph, 3)


def test_compute_note_lemmas_uses_configured_min_length():
    lemmas = compute_note_lemmas("a cat wearing a hat")

    assert "cat" in lemmas and "hat" in lemmas
