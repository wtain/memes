"""Generates batch/data/taxonomy/similarity.json: within-axis neighbours of the vocabulary keys, from the
Ollama embeddings of their definitions (P1 spec, decision 8).

    PYTHONIOENCODING=utf-8 python -m batch.build_taxonomy_similarity --env general [--threshold 0.75]

The file is committed so a bad neighbour pair shows up in a diff. Re-run after any vocabulary change or
embedding-model change; tests/taxonomy/test_shipped_similarity.py fails when it is stale.
"""
import argparse
import json

from ai.ollama_embed import OllamaEmbedder
from config.settings import load_env, settings
from taxonomy.loader import DEFAULT_DIR, load
from taxonomy.similarity import build_artifact, neighbour_stats

OUTPUT = DEFAULT_DIR / "similarity.json"


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    parser.add_argument("--threshold", type=float, default=None,
                        help="override settings.TAXONOMY.SIMILARITY_THRESHOLD")
    parser.add_argument("--model", default=None, help="override settings.TAXONOMY.EMBEDDING_MODEL")
    parser.add_argument("--dry-run", action="store_true", help="print stats, do not write the file")
    args = parser.parse_args(argv)
    load_env(args.env)

    model = args.model or settings.TAXONOMY.EMBEDDING_MODEL
    threshold = args.threshold if args.threshold is not None else settings.TAXONOMY.SIMILARITY_THRESHOLD
    embedder = OllamaEmbedder(model)
    artifact = build_artifact(load(), embedder, threshold=threshold, model_name=model,
                              model_digest=embedder.model_digest())

    stats = neighbour_stats(artifact)
    print(f"threshold={threshold} median_neighbours={stats['median']} max={stats['max']} "
          f"isolated={len(stats['isolated'])} {stats['isolated']}")
    if args.dry_run:
        return
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    main()
