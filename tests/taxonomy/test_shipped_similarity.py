import json

from taxonomy.loader import DEFAULT_DIR, load
from taxonomy.similarity import check_artifact


def test_shipped_similarity_artifact_matches_the_shipped_taxonomy():
    artifact = json.loads((DEFAULT_DIR / "similarity.json").read_text(encoding="utf-8"))
    problems = check_artifact(artifact, load())
    assert problems == [], "regenerate with `python -m batch.build_taxonomy_similarity`: " + "; ".join(problems)
