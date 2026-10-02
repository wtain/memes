from pathlib import Path

import pytest

from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


def test_vocabulary_prompt_lists_key_and_definition_in_file_order():
    assert vocabulary_prompt("emotion", load(FIXTURE)) == "- joy: Plain happiness.\n- anger: Strong hostility."


def test_vocabulary_prompt_rejects_free_text_axis():
    with pytest.raises(ValueError, match="situation"):
        vocabulary_prompt("situation", load(FIXTURE))


def test_vocabulary_prompt_rejects_unknown_axis():
    with pytest.raises(ValueError, match="nope"):
        vocabulary_prompt("nope", load(FIXTURE))
