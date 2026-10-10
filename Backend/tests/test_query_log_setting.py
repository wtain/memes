import pytest

from config.settings import _build


@pytest.mark.parametrize("environment", ["metal", "general", "it"])
def test_the_query_log_switch_is_defined_for_every_environment(environment):
    assert _build(environment).get("search.meme_from_text.query_log.enabled") is True
