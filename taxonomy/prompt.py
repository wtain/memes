from taxonomy.loader import load
from taxonomy.model import VOCABULARY, Taxonomy


def vocabulary_prompt(axis: str, tax: Taxonomy | None = None) -> str:
    """Render an axis's choice list (key plus definition) for LLM prompts, in file order."""
    tax = tax or load()
    spec = tax.axes.get(axis)
    if spec is None or spec.type != VOCABULARY:
        raise ValueError(f"{axis!r} is not a controlled-vocabulary axis")
    return "\n".join(f"- {key}: {value.definition}" for key, value in tax.values[axis].items())
