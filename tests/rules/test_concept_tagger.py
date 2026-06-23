"""
Unit tests for rules.concept_tagger.ConceptTagger.

Each test builds minimal YAML files in a tmp_path so there are no external
fixture dependencies.  The focus is on correctness of the matching and
normalisation logic, not on any real-world rule file.
"""

import pytest
import yaml

from rules.concept_tagger import ConceptTagger


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path, concepts: dict, tags: dict | None = None) -> ConceptTagger:
    """Write minimal YAML files and return a loaded ConceptTagger."""
    if tags is None:
        # Collect all vote keys from concepts and declare them automatically.
        all_tags: set[str] = set()
        for cfg in concepts.values():
            all_tags.update((cfg.get("votes") or {}).keys())
        tags = {t: {} for t in sorted(all_tags)}

    tags_path = tmp_path / "tags.test.yaml"
    tags_path.write_text(
        yaml.dump({"defaults": {"threshold": 1.0}, "tags": tags}, allow_unicode=True),
        encoding="utf-8",
    )
    concepts_path = tmp_path / "concepts.test.yaml"
    concepts_path.write_text(yaml.dump(concepts, allow_unicode=True), encoding="utf-8")

    return ConceptTagger.load(tmp_path, "test")


def _tags(engine: ConceptTagger, text: str) -> set[str]:
    return {f"{k}:{v}" for k, v in engine.tag(text).tags}


# ---------------------------------------------------------------------------
# single-word matching
# ---------------------------------------------------------------------------

class TestSingleWord:
    def test_exact_match(self, tmp_path):
        e = _make_engine(tmp_path, {
            "metallica": {"words": ["metallica"], "votes": {"band:metallica": 1.0}},
        })
        assert "band:metallica" in _tags(e, "Metallica is great")

    def test_no_match_on_different_word(self, tmp_path):
        e = _make_engine(tmp_path, {
            "cat": {"words": ["кот"], "votes": {"animal:cat": 1.0}},
        })
        # "который" lemmatises to "который", not "кот"
        assert "animal:cat" not in _tags(e, "который пришёл")

    def test_case_insensitive_via_lemmatisation(self, tmp_path):
        e = _make_engine(tmp_path, {
            "metallica": {"words": ["metallica"], "votes": {"band:metallica": 1.0}},
        })
        assert "band:metallica" in _tags(e, "METALLICA RULES")

    def test_inflected_russian_word(self, tmp_path):
        e = _make_engine(tmp_path, {
            "work": {"words": ["работа"], "votes": {"тема:работа": 1.0}},
        })
        # "работе" lemmatises to "работа"
        assert "тема:работа" in _tags(e, "на работе сегодня")

    def test_no_match_substring(self, tmp_path):
        """Concept word must appear as its own token, not as a substring."""
        e = _make_engine(tmp_path, {
            "cat": {"words": ["кот"], "votes": {"animal:cat": 1.0}},
        })
        # "кот" is embedded inside "мокотов" — should not match
        assert "animal:cat" not in _tags(e, "МОКОТОВ")


# ---------------------------------------------------------------------------
# multi-word phrase matching
# ---------------------------------------------------------------------------

class TestPhraseMatching:
    def test_two_word_phrase(self, tmp_path):
        e = _make_engine(tmp_path, {
            "alice": {"words": ["alice in chains"], "votes": {"band:aic": 1.0}},
        })
        assert "band:aic" in _tags(e, "listening to Alice in Chains today")

    def test_phrase_with_function_word(self, tmp_path):
        """Short function words ('of', 'in', 'a') must not break phrase matching."""
        e = _make_engine(tmp_path, {
            "soad": {"words": ["system of a down"], "votes": {"band:soad": 1.0}},
        })
        assert "band:soad" in _tags(e, "System of a Down concert was insane")

    def test_phrase_requires_all_words(self, tmp_path):
        """A phrase only fires when every part is present."""
        e = _make_engine(tmp_path, {
            "zzz": {"words": ["zz top"], "votes": {"band:zztop": 1.0}},
        })
        # "top" alone must not fire "band:zztop"
        assert "band:zztop" not in _tags(e, "she reached the top")

    def test_phrase_fires_when_all_parts_present(self, tmp_path):
        e = _make_engine(tmp_path, {
            "zzz": {"words": ["zz top"], "votes": {"band:zztop": 1.0}},
        })
        assert "band:zztop" in _tags(e, "ZZ Top played Sharp Dressed Man")


# ---------------------------------------------------------------------------
# voting and threshold logic
# ---------------------------------------------------------------------------

class TestVotingAndThreshold:
    def test_single_concept_fires_tag(self, tmp_path):
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0, "genre:thrash": 1.0}},
        })
        tags = _tags(e, "Slayer tour 2024")
        assert "band:slayer" in tags
        assert "genre:thrash" in tags

    def test_threshold_not_reached_suppresses_tag(self, tmp_path):
        tags_yaml = {"band:rare": {"threshold": 2.0}}
        concepts_yaml = {
            "c1": {"words": ["alpha"], "votes": {"band:rare": 1.0}},
        }
        e = _make_engine(tmp_path, concepts_yaml, tags=tags_yaml)
        assert "band:rare" not in _tags(e, "alpha beta gamma")

    def test_two_concepts_reach_threshold(self, tmp_path):
        tags_yaml = {"band:rare": {"threshold": 2.0}}
        concepts_yaml = {
            "c1": {"words": ["alpha"], "votes": {"band:rare": 1.0}},
            "c2": {"words": ["beta"], "votes": {"band:rare": 1.0}},
        }
        e = _make_engine(tmp_path, concepts_yaml, tags=tags_yaml)
        assert "band:rare" in _tags(e, "alpha beta gamma")

    def test_empty_text_produces_no_tags(self, tmp_path):
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0}},
        })
        assert _tags(e, "") == set()
        assert _tags(e, "   ") == set()


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------

class TestTrace:
    def test_trace_records_matching_concept(self, tmp_path):
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0}},
        })
        result = e.tag("Slayer rules")
        assert len(result.trace) == 1
        assert result.trace[0]["concept"] == "slayer"

    def test_trace_empty_on_no_match(self, tmp_path):
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0}},
        })
        assert e.tag("nothing here").trace == []


# ---------------------------------------------------------------------------
# OCR artifact normalisation (rules/normalize.py)
# ---------------------------------------------------------------------------

class TestOCRArtifacts:
    def test_subreddit_prefix(self, tmp_path):
        """'rimetallica' (r/Metallica OCR'd with slash as 'i') still fires."""
        e = _make_engine(tmp_path, {
            "metallica": {"words": ["metallica"], "votes": {"band:metallica": 1.0}},
        })
        assert "band:metallica" in _tags(e, "rimetallica")

    def test_subreddit_prefix_short_suffix_ignored(self, tmp_path):
        """Suffix must be >= 5 chars to avoid spurious splits of short words."""
        e = _make_engine(tmp_path, {
            "rip": {"words": ["rip"], "votes": {"слово:rip": 1.0}},
        })
        # "ricat" → suffix "cat" (3 chars) < 5 → no split; no "rip" concept fires
        assert "слово:rip" not in _tags(e, "ricat")

    def test_doubled_trailing_char(self, tmp_path):
        """'slayerll' (SLAYER!! OCR'd) still fires the slayer concept."""
        e = _make_engine(tmp_path, {
            "slayer": {"words": ["slayer"], "votes": {"band:slayer": 1.0}},
        })
        assert "band:slayer" in _tags(e, "slayerll")

    def test_underscore_splits_handle(self, tmp_path):
        """'varg_vikernes' splits on underscore so 'varg' enters the lemma bag."""
        e = _make_engine(tmp_path, {
            "burzum": {"words": ["varg"], "votes": {"band:burzum": 1.0}},
        })
        assert "band:burzum" in _tags(e, "varg_vikernes posted again")

    def test_normal_word_not_split_by_doubled_char_heuristic(self, tmp_path):
        """'will' ends in 'll' but stripping gives 'wi' (< min_length) — no ghost lemma."""
        e = _make_engine(tmp_path, {
            "wi": {"words": ["wi"], "votes": {"ghost:wi": 1.0}},
        })
        # "wi" (2 chars) would be below min_length=3, so it never enters the bag
        assert "ghost:wi" not in _tags(e, "will you come")
