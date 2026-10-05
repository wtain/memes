"""Evaluation CLI (P6 spec, decision 1).

    python -m evaluation <command> ...

Database commands read only. They require APP_ENV and the READ-ONLY DATABASE_URL (role ocr_readonly) to be set in
the shell BEFORE starting, and deliberately never call config.settings.load_env(): that function loads
environments/.env.<env> with override=True and would replace the read-only URL with the write-capable one.
"""
import argparse
import asyncio
import os
import sys
from datetime import date as date_class
from pathlib import Path

from evaluation.gold import (
    GoldError, Judgment, file_sha256, judgments_by_query, load_corpus, load_judgments, load_queries,
    merge_judgments, parse_export, save_corpus, save_judgments, Corpus,
)
from evaluation.report import (
    CompareError, compare_baselines, format_report, git_commit, load_baseline, make_baseline, pooling_bias,
    save_baseline,
)

DEFAULT_GOLD = Path("evaluation/gold")
DEFAULT_WORK = Path("evaluation/work")
MIN_PROFILED_SHARE = 0.95
RETRIEVER_NAMES = ("random", "lexical_and", "lexical_or", "situation_embedding", "axis_match", "parsed_axis_match")
POOL_SOURCES = ("lexical_or", "situation_embedding", "axis_match")
POOL_DEPTH = 30


def require_readonly() -> None:
    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        sys.exit("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly) and APP_ENV must be set.")


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def check_environment(app_env: str | None, corpus_environment: str) -> str | None:
    """A refusal message when the shell's APP_ENV names a different environment than the corpus was frozen for."""
    if app_env and app_env != corpus_environment:
        return (f"APP_ENV is {app_env!r} but the corpus was built for {corpus_environment!r}; scoring a different "
                "database than the one the gold data was judged against is meaningless")
    return None


def resolve_corpus_paths(gold: Path, out: str | None, ids_file: str | None) -> tuple[Path, Path]:
    return (Path(out) if out else gold / "corpus.json"), (Path(ids_file) if ids_file else DEFAULT_WORK / "corpus.ids")


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def _baseline_path(gold: Path, label_or_path: str) -> Path:
    candidate = Path(label_or_path)
    if candidate.suffix == ".json" and candidate.exists():
        return candidate
    return gold / "baselines" / f"{label_or_path}.json"


# ------------------------------------------------------------------ commands without a database

def cmd_import_judgments(args, gold: Path) -> int:
    queries = load_queries(gold / "queries.json")
    corpus = load_corpus(gold / "corpus.json")
    existing = load_judgments(gold / "judgments.json")
    incoming: list[Judgment] = []
    import json
    for name in args.files:
        with open(name, encoding="utf-8") as handle:
            data = json.load(handle)
        incoming.extend(parse_export(data, valid_query_ids={q.id for q in queries},
                                     valid_image_ids=set(corpus.image_ids())))
    result = merge_judgments(existing, incoming)
    save_judgments(result.merged, gold / "judgments.json")
    print(f"added {result.added}, overwritten {result.overwritten}, unchanged {result.unchanged}; "
          f"{len(result.merged)} judgments in total")
    return 0


def cmd_compare(args, gold: Path) -> int:
    paths = [_baseline_path(gold, args.a), _baseline_path(gold, args.b)]
    for name, path in zip((args.a, args.b), paths):
        if not path.exists():
            return _error(f"no baseline named {name!r} ({path})")
    queries = {q.id: q.text for q in load_queries(gold / "queries.json")} if (gold / "queries.json").exists() else {}
    print(compare_baselines(load_baseline(paths[0]), load_baseline(paths[1]), force=args.force, query_texts=queries))
    return 0


def cmd_list_baselines(args, gold: Path) -> int:
    for path in sorted((gold / "baselines").glob("*.json")):
        baseline = load_baseline(path)
        ndcg = baseline["summary"]["overall"]["ndcg"]
        shown = "-" if ndcg is None else f"{ndcg:.3f}"
        print(f"{baseline['label']}\t{baseline['retriever']['name']}\t{baseline['date']}\tnDCG@10 {shown}")
    return 0


def cmd_sheet(args, gold: Path) -> int:
    import json
    from evaluation.sheet import make_thumbnail, pools_from_dict, pools_round_path, render_judging_sheet
    pools_file = pools_round_path(args.pools_dir, args.round)
    if not pools_file.exists():
        return _error(f"{pools_file} does not exist; run build-pools --round {args.round} first")
    with open(pools_file, encoding="utf-8") as handle:
        data = json.load(handle)
    pools, filenames = pools_from_dict(data)
    queries = load_queries(gold / "queries.json")
    thumbnails = {image_id: make_thumbnail(os.path.join(args.base_path, name)) for image_id, name in filenames.items()}
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    languages = sorted({q.language for q in queries if q.id in pools})
    for language in languages:
        html = render_judging_sheet(
            language=language, queries=queries, pools={qid: pool.candidates for qid, pool in pools.items()},
            thumbnails=thumbnails, rater=args.rater, date=args.date or date_class.today().isoformat(),
            round_number=data["round"])
        target = out_dir / f"judging-{language}-r{data['round']}.html"
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(html)
        print(f"wrote {target}")
    missing = sum(1 for thumbnail in thumbnails.values() if thumbnail is None)
    if missing:
        _warn(f"{missing} of {len(thumbnails)} candidate images have no thumbnail (file missing or unreadable); "
              "the sheet shows '(no image)' for them")
    return 0


# ------------------------------------------------------------------ database commands

def _settings_config():
    from batch.utils.meme_profile_config import load_config
    from config.settings import settings
    config = load_config(settings.get)
    if config is None:
        sys.exit("meme_profiles is not configured for this environment (set APP_ENV to general or metal)")
    return config


def _embedder_and_model():
    from ai.ollama_embed import OllamaEmbedder
    from config.settings import settings
    model_name = settings.TAXONOMY.EMBEDDING_MODEL
    return OllamaEmbedder(model_name), model_name


def _build_parser(model: str, timeout: float | None = None):
    """A QueryParser for `model` using the tracked parse.* settings; `timeout` overrides parse.timeout_seconds
    (used to diagnose slow models). Experiments never edit settings: the model comes from the command line."""
    from config.settings import settings
    from parse.config import load_config
    from parse.parser import QueryParser

    def get(key, default=None):
        if key == "parse.model":
            return model
        if key == "parse.timeout_seconds" and timeout is not None:
            return timeout
        return settings.get(key, default)

    return QueryParser(load_config(get))


def _make_retriever(name: str, session, seed: int, parse_model: str | None = None):
    from evaluation.retrievers.axis_match import AxisMatchRetriever
    from evaluation.retrievers.lexical import LexicalRetriever
    from evaluation.retrievers.random_retriever import RandomRetriever
    from evaluation.retrievers.situation_embedding import SituationEmbeddingRetriever
    if name == "random":
        return RandomRetriever(seed)
    if name == "lexical_and":
        return LexicalRetriever(session, "and")
    if name == "lexical_or":
        return LexicalRetriever(session, "or")
    if name == "axis_match":
        return AxisMatchRetriever(session)
    if name == "situation_embedding":
        embedder, model_name = _embedder_and_model()
        return SituationEmbeddingRetriever(session, embedder, model_name)
    if name == "parsed_axis_match":
        from evaluation.retrievers.parsed_axis_match import ParsedAxisMatchRetriever
        return ParsedAxisMatchRetriever(session, _build_parser(parse_model))
    raise ValueError(f"unknown retriever {name!r}")


async def _check_corpus(session, corpus: Corpus, *, check_digest: bool = False) -> dict:
    from evaluation.state import collect_corpus_drift, collect_profile_state, embedding_digest_warnings
    refusal = check_environment(os.environ.get("APP_ENV"), corpus.environment)
    if refusal:
        sys.exit(refusal)
    drift = await collect_corpus_drift(session, corpus)
    if any(drift.values()):
        _warn(f"the database has moved from the frozen corpus: {drift['missing']} images missing, "
              f"{drift['inactive']} no longer active, {drift['hashChanged']} with a changed content hash")
    state = await collect_profile_state(session, list(corpus.image_ids()))
    if state["profiled"] < MIN_PROFILED_SHARE * len(corpus.images):
        sys.exit(f"Only {state['profiled']} of {len(corpus.images)} corpus images have a profile (need "
                 f"{MIN_PROFILED_SHARE:.0%}). Profile them with: python -m batch.build_meme_profiles --env general "
                 "--ids-file <corpus.ids>, then python -m batch.build_meme_profile_embeddings --ids-file <corpus.ids>")
    if state["embedded"] < MIN_PROFILED_SHARE * state["profiled"]:
        _warn(f"only {state['embedded']} of {state['profiled']} profiled images have a situation embedding; "
              "situation_embedding results will be incomplete. Fix: python -m batch.build_meme_profile_embeddings "
              "--env general --ids-file <corpus.ids>")
    if check_digest:
        embedder, model_name = _embedder_and_model()
        for message in embedding_digest_warnings(state["embeddingModels"], model_name, embedder.model_digest()):
            _warn(message)
    return state


async def cmd_build_corpus(args, gold: Path) -> int:
    require_readonly()
    from evaluation.corpus import load_candidates, quotas_for, select_corpus, write_ids_file
    from Storage.db import AsyncSessionLocal
    target, ids_target = resolve_corpus_paths(gold, args.out, args.ids_file)
    if target.exists() and not args.force:
        return _error(f"{target} already exists; the corpus is frozen (use --force to replace it deliberately)")
    config = _settings_config()
    async with AsyncSessionLocal() as session:
        candidates = await load_candidates(session, config)
    images = select_corpus(candidates, quotas_for(args.size), args.seed)
    corpus = Corpus(environment=args.environment, seed=args.seed, created=date_class.today().isoformat(),
                    images=tuple(images))
    save_corpus(corpus, target)
    ids_target.parent.mkdir(parents=True, exist_ok=True)
    write_ids_file(ids_target, list(corpus.image_ids()))
    if len(images) < args.size:
        _warn(f"only {len(images)} eligible images could be selected, fewer than the requested {args.size}")
    counts: dict[str, int] = {}
    for image in images:
        counts[image.stratum] = counts.get(image.stratum, 0) + 1
    print(f"{len(candidates)} eligible images; corpus of {len(images)}: {counts}; wrote {target} and {ids_target}")
    return 0


async def cmd_build_pools(args, gold: Path) -> int:
    require_readonly()
    from evaluation.sheet import pools_round_path
    pools_file = pools_round_path(args.pools_dir, args.round)
    if pools_file.exists() and not args.force:
        return _error(f"{pools_file} already exists; pools are append-only across rounds (use --force to replace "
                      "this round's file deliberately)")
    import json
    from sqlalchemy import select
    from evaluation.pooling import build_pool
    from evaluation.retrievers.random_retriever import RandomRetriever
    from evaluation.sheet import pools_to_dict
    from Storage.db import AsyncSessionLocal
    from Storage.models import Image
    corpus = load_corpus(gold / "corpus.json")
    queries = [q for q in load_queries(gold / "queries.json") if q.reviewed]
    judged = judgments_by_query(load_judgments(gold / "judgments.json"))
    corpus_ids = list(corpus.image_ids())
    pools = {}
    async with AsyncSessionLocal() as session:
        await _check_corpus(session, corpus, check_digest=True)
        retrievers = {name: _make_retriever(name, session, args.seed) for name in POOL_SOURCES}
        randomiser = RandomRetriever(args.seed)
        for retriever in retrievers.values():
            if hasattr(retriever, "prepare"):
                await retriever.prepare(queries)
        for query in queries:
            ranked = {name: [image_id for image_id, _ in await retriever.retrieve(query, corpus_ids, POOL_DEPTH)]
                      for name, retriever in retrievers.items()}
            random_ids = [image_id for image_id, _ in await randomiser.retrieve(query, corpus_ids, 50)]
            ranked_target = args.per_query - 3
            pools[query.id] = build_pool(query.id, ranked, random_ids, exclude=set(judged.get(query.id, {})),
                                         ranked_target=ranked_target, random_target=3, seed=args.seed)
        import uuid
        wanted = {uuid.UUID(image_id) for pool in pools.values() for image_id in pool.candidates}
        rows = await session.execute(select(Image.id, Image.filename).where(Image.id.in_(wanted)))
        filenames = {str(image_id): filename for image_id, filename in rows.all()}
    pools_file.parent.mkdir(parents=True, exist_ok=True)
    with open(pools_file, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(pools_to_dict(args.round, args.per_query, pools, filenames), indent=2,
                                ensure_ascii=False) + "\n")
    print(f"wrote {pools_file}: {sum(len(p.candidates) for p in pools.values())} candidates for {len(pools)} queries")
    return 0


async def cmd_run(args, gold: Path) -> int:
    if args.retriever == "parsed_axis_match" and not args.parse_model:
        return _error("--parse-model is required for the parsed_axis_match retriever")
    require_readonly()
    from evaluation.runner import run_retriever, summarize
    from Storage.db import AsyncSessionLocal
    corpus = load_corpus(gold / "corpus.json")
    queries = load_queries(gold / "queries.json")
    judgments = load_judgments(gold / "judgments.json")
    target = gold / "baselines" / f"{args.label}.json"
    if target.exists() and not args.force:
        return _error(f"{target} already exists (use --force to replace it)")
    async with AsyncSessionLocal() as session:
        state = await _check_corpus(session, corpus, check_digest=args.retriever == "situation_embedding")
        retriever = _make_retriever(args.retriever, session, args.seed, args.parse_model)
        results = await run_retriever(retriever, queries, list(corpus.image_ids()), judgments_by_query(judgments),
                                      k=args.k)
    summary = summarize(results, corpus.strata(), judgments_by_query(judgments), k=args.k)
    baseline = make_baseline(
        label=args.label, retriever_name=retriever.name, retriever_config=retriever.config,
        date=date_class.today().isoformat(), git_commit=git_commit(),
        gold_hashes={"corpusSha": file_sha256(gold / "corpus.json"), "queriesSha": file_sha256(gold / "queries.json"),
                     "judgmentsSha": file_sha256(gold / "judgments.json")},
        profile_state=state, results=results, summary=summary)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_baseline(target, baseline)
    bias = None
    from evaluation.sheet import load_pool_sources
    sources = load_pool_sources(args.pools_dir)
    if sources:
        bias = pooling_bias(results, sources)
    print(format_report(baseline, bias))
    print(f"wrote {target}")
    return 0


async def cmd_parse_eval(args, gold: Path) -> int:
    """Runs the query parser over the reviewed gold queries and scores it against their expected profiles. Needs a
    live Ollama (parser and embedding model); does not touch the database."""
    import asyncio as _asyncio
    from evaluation.parse_scoring import build_parse_report, format_parse_report, save_parse_report, score_query
    from parse.parser import meta_to_dict
    from parse.prompt import PARSER_VERSION

    target = gold / "parse" / f"{args.label}.json"
    if target.exists() and not args.force:
        return _error(f"{target} already exists (use --force to replace it)")
    queries = [q for q in load_queries(gold / "queries.json") if q.reviewed]
    if args.max_queries:
        queries = queries[:args.max_queries]
    parser = _build_parser(args.model, args.timeout_seconds)
    await parser.warm_up()      # not scored: loads the model

    results = []
    for query in queries:
        results.append(await parser.parse(query.text))

    similarity: dict[int, float] = {}
    embedder, _model_name = _embedder_and_model()
    indexed = [(i, r.profile["situation"]) for i, r in enumerate(results) if r.profile.get("situation")]
    if indexed:
        parsed_vectors = await _asyncio.to_thread(embedder.embed, [text for _, text in indexed])
        expected_vectors = await _asyncio.to_thread(embedder.embed, [queries[i].expected.situation for i, _ in indexed])
        for k, (i, _text) in enumerate(indexed):
            similarity[i] = float(parsed_vectors[k] @ expected_vectors[k])

    scores, parsed = [], {}
    for i, (query, result) in enumerate(zip(queries, results)):
        scores.append(score_query(query.id, query.language, query.expected, result.profile,
                                  meta_to_dict(result.meta), similarity.get(i)))
        parsed[query.id] = {"situation": result.profile.get("situation"),
                            **{axis: [v["key"] for v in result.profile[axis]]
                               for axis in ("emotion", "stance", "topic", "tone")}}
    report = build_parse_report(
        label=args.label, date=date_class.today().isoformat(), model=args.model, parser_version=PARSER_VERSION,
        taxonomy_version=results[0].profile["taxonomyVersion"] if results else 0,
        queries_sha=file_sha256(gold / "queries.json"), scores=scores, parsed=parsed)
    target.parent.mkdir(parents=True, exist_ok=True)
    save_parse_report(target, report)
    print(format_parse_report(report))
    print(f"wrote {target}")
    return 0


# ------------------------------------------------------------------ entry point

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    parser.add_argument("--gold-dir", default=str(DEFAULT_GOLD))
    commands = parser.add_subparsers(dest="command", required=True)

    corpus = commands.add_parser("build-corpus")
    corpus.add_argument("--size", type=int, default=1500)
    corpus.add_argument("--seed", type=int, default=7)
    corpus.add_argument("--environment", default="general")
    corpus.add_argument("--out", default=None, help="default: <gold-dir>/corpus.json")
    corpus.add_argument("--ids-file", default=None, help=f"default: {DEFAULT_WORK / 'corpus.ids'}")
    corpus.add_argument("--force", action="store_true")

    pools = commands.add_parser("build-pools")
    pools.add_argument("--round", type=int, required=True)
    pools.add_argument("--per-query", type=int, default=12)
    pools.add_argument("--seed", type=int, default=7)
    pools.add_argument("--pools-dir", default=str(DEFAULT_WORK))
    pools.add_argument("--force", action="store_true")

    sheet = commands.add_parser("sheet")
    sheet.add_argument("--round", type=int, required=True)
    sheet.add_argument("--pools-dir", default=str(DEFAULT_WORK))
    sheet.add_argument("--out-dir", default=str(DEFAULT_WORK / "sheets"))
    sheet.add_argument("--base-path", required=True)
    sheet.add_argument("--rater", required=True)
    sheet.add_argument("--date", default=None)

    imports = commands.add_parser("import-judgments")
    imports.add_argument("files", nargs="+")

    run = commands.add_parser("run")
    run.add_argument("--retriever", required=True, choices=RETRIEVER_NAMES)
    run.add_argument("--label", required=True)
    run.add_argument("--k", type=int, default=10)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--pools-dir", default=str(DEFAULT_WORK))
    run.add_argument("--force", action="store_true")
    run.add_argument("--parse-model", default=None, help="model for the parsed_axis_match retriever")

    compare = commands.add_parser("compare")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--force", action="store_true")

    parse_eval = commands.add_parser("parse-eval")
    parse_eval.add_argument("--model", required=True)
    parse_eval.add_argument("--label", required=True)
    parse_eval.add_argument("--max-queries", type=int, default=None)
    parse_eval.add_argument("--timeout-seconds", type=float, default=None)
    parse_eval.add_argument("--force", action="store_true")

    commands.add_parser("list-baselines")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    gold = Path(args.gold_dir)
    handlers = {"import-judgments": cmd_import_judgments, "compare": cmd_compare, "list-baselines": cmd_list_baselines,
                "sheet": cmd_sheet}
    async_handlers = {"build-corpus": cmd_build_corpus, "build-pools": cmd_build_pools, "run": cmd_run,
                      "parse-eval": cmd_parse_eval}
    try:
        if args.command in handlers:
            return handlers[args.command](args, gold)
        return asyncio.run(async_handlers[args.command](args, gold))
    except (GoldError, CompareError) as error:
        return _error(str(error))


if __name__ == "__main__":
    sys.exit(main())
