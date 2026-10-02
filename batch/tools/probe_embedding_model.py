"""One-off sanity probe for the Ollama embedding model (P1 spec, decision 6).

Not a CI test (needs a running Ollama with the model pulled):
    python -m batch.tools.probe_embedding_model --model bge-large

Prints the model digest and output dimension, then checks that each similar phrase pair is closer
than the closest unrelated phrase by MARGIN. Exit code 1 if any pair fails.
"""
import argparse
import sys

import numpy as np

from ai.ollama_embed import OllamaEmbedder

SIMILAR_PAIRS = [
    ("my boss moved the deadline to tomorrow", "sudden deadline pressure at work"),
    ("plans got cancelled at the last minute", "unexpected cancellation of an evening out"),
    ("I finally passed the exam", "relief after getting a good grade"),
    ("someone ate my lunch from the office fridge", "food stolen at work"),
]
UNRELATED = [
    "a cat sitting on a windowsill in the sun",
    "a recipe for tomato soup",
    "the football match ended in a draw",
    "the train timetable changed for summer",
]
MARGIN = 0.05


def evaluate(embedder) -> list[tuple[str, float, float, bool]]:
    firsts = embedder.embed([a for a, _ in SIMILAR_PAIRS])
    seconds = embedder.embed([b for _, b in SIMILAR_PAIRS])
    unrelated = embedder.embed(UNRELATED)
    rows = []
    for i, (phrase, _) in enumerate(SIMILAR_PAIRS):
        positive = float(firsts[i] @ seconds[i])
        negative = float(np.max(unrelated @ firsts[i]))
        rows.append((phrase, positive, negative, positive > negative + MARGIN))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="bge-large")
    args = parser.parse_args(argv)

    embedder = OllamaEmbedder(args.model)
    print(f"model={args.model} digest={embedder.model_digest()}")
    print(f"dimension={embedder.embed(['dimension probe']).shape[1]}")
    rows = evaluate(embedder)
    for phrase, positive, negative, ok in rows:
        print(f"{'PASS' if ok else 'FAIL'}  pair={positive:.3f}  closest_unrelated={negative:.3f}  {phrase}")
    return 0 if all(ok for *_, ok in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
