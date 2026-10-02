from taxonomy.lint import lint
from taxonomy.loader import load
from taxonomy.prompt import vocabulary_prompt


def test_shipped_taxonomy_loads_and_lints_clean():
    tax = load()
    assert tax.version >= 1
    assert lint(tax) == []


def test_shipped_axes_are_the_confirmed_five():
    assert list(load().axes) == ["situation", "emotion", "stance", "topic", "tone"]


def test_every_controlled_axis_renders_a_prompt():
    tax = load()
    for axis in tax.controlled_axes():
        prompt = vocabulary_prompt(axis, tax)
        assert prompt.count("\n") + 1 == len(tax.values[axis])
