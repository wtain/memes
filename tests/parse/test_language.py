import pytest

from parse.language import guess_language


@pytest.mark.parametrize("text, expected", [
    ("Привет, как дела?", "ru"),
    ("Мой босс снова перенёс дедлайн, wow", "ru"),
    ("¿Qué opinas de esto?", "es"),
    ("El gato está en la casa con mi amigo", "es"),
    ("Mañana tengo examen", "es"),
    ("My boss moved the deadline again", "en"),
    ("Let me grab a café", "en"),
    ("12345 !!!", "other"),
    ("", "other"),
])
def test_script_based_guess(text, expected):
    assert guess_language(text) == expected


def test_the_hint_is_used_only_when_nothing_can_be_guessed():
    assert guess_language("12345", hint="es") == "es"
    assert guess_language("12345", hint="xx") == "other"
    assert guess_language("Привет", hint="en") == "ru"
