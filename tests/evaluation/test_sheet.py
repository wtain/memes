import json
import re

from evaluation.gold import ExpectedProfile, GoldQuery
from evaluation.pooling import Pool
from evaluation.sheet import pools_from_dict, pools_to_dict, render_judging_sheet

EXPECTED = ExpectedProfile("hidden situation", ("annoyance",), ("mock",), ("work",), ("sarcastic",))
IMG_A, IMG_B = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def _query(query_id, language="en", text="My boss moved the deadline <b>again</b>"):
    return GoldQuery(query_id, language, text, "short", "easy", EXPECTED, True)


def _sheet(**overrides):
    kwargs = dict(language="en", queries=[_query("q001"), _query("q002", language="ru", text="Привет")],
                  pools={"q001": [IMG_A, IMG_B], "q002": [IMG_B]},
                  thumbnails={IMG_A: "data:image/jpeg;base64,AAAA", IMG_B: None},
                  rater="ramiz", date="2026-10-04", round_number=1)
    kwargs.update(overrides)
    return render_judging_sheet(**kwargs)


def test_only_the_requested_languages_queries_are_shown_and_text_is_escaped():
    html = _sheet()

    assert "My boss moved the deadline &lt;b&gt;again&lt;/b&gt;" in html
    assert "Привет" not in html
    assert "<b>again</b>" not in html


def test_sheet_is_blinded_no_retriever_names_and_no_profile_values():
    html = _sheet().lower()

    for forbidden in ("lexical", "situation_embedding", "axis_match", "random", "hidden situation",
                      "annoyance", "sarcastic", "expectedprofile"):
        assert forbidden not in html


def test_each_candidate_has_three_grades_and_carries_ids_in_data_attributes():
    html = _sheet()

    assert html.count("data-q='q001'") == 2 * 3                       # two candidates x three grades
    assert f"data-i='{IMG_A}'" in html and f"data-i='{IMG_B}'" in html
    for label in ("2 great reply", "1 acceptable", "0 wrong"):
        assert label in html
    assert "data:image/jpeg;base64,AAAA" in html
    assert "(no image)" in html                                       # IMG_B has no thumbnail


def test_the_export_button_and_metadata_are_present_and_progress_is_stored_safely():
    html = _sheet()

    assert "Download judgments-en.json" in html or "judgments-en.json" in html
    meta = json.loads(re.search(r"<script type='application/json' id='meta'>(.*?)</script>", html, re.S).group(1))
    assert meta == {"language": "en", "rater": "ramiz", "date": "2026-10-04", "round": 1}
    assert "localStorage" in html and "try" in html


def test_a_closing_script_tag_in_the_rater_name_cannot_break_out_of_the_metadata_block():
    html = _sheet(rater="</script><script>alert(1)</script>")

    assert "<script>alert(1)</script>" not in html


def test_pools_file_round_trip_keeps_candidates_sources_and_filenames():
    pools = {"q001": Pool("q001", (IMG_B, IMG_A), {IMG_A: ("lexical_or",), IMG_B: ("axis_match", "random")})}
    filenames = {IMG_A: "a.jpg", IMG_B: "b.jpg"}

    data = pools_to_dict(2, 12, pools, filenames)
    restored_pools, restored_names = pools_from_dict(data)

    assert data["round"] == 2 and data["perQuery"] == 12
    assert restored_pools == pools and restored_names == filenames
