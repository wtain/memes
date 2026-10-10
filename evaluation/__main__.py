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
    GoldError, file_sha256, judgments_by_query, load_corpus, load_judgments, load_queries, save_corpus, Corpus,
)
from evaluation.ratings_job import check_environment, export_ratings, import_judgment_files, prepare_output_dir
from evaluation.report import (
    CompareError, compare_baselines, format_report, git_commit, load_baseline, make_baseline, pooling_bias,
    save_baseline,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GOLD = Path("evaluation/gold")
DEFAULT_WORK = Path("evaluation/work")
MIN_PROFILED_SHARE = 0.95
RETRIEVER_NAMES = ("random", "lexical_and", "lexical_or", "situation_embedding", "axis_match", "parsed_axis_match",
                   "fused")
POOL_SOURCES = ("lexical_or", "situation_embedding", "axis_match")
POOL_DEPTH = 30
POOL_SOURCE_CHOICES = POOL_SOURCES + ("parsed_axis_match", "fused")
DEFAULT_POOL_SOURCES = ",".join(POOL_SOURCES)


def pool_source_names(sources_arg: str) -> list[str]:
    return [name.strip() for name in sources_arg.split(",") if name.strip()]


def validate_pool_args(args) -> str | None:
    names = pool_source_names(args.sources)
    if not names:
        return "--sources must name at least one source"
    unknown = [name for name in names if name not in POOL_SOURCE_CHOICES]
    if unknown:
        return f"unknown pool source(s): {', '.join(unknown)} (choose from {', '.join(POOL_SOURCE_CHOICES)})"
    if ("parsed_axis_match" in names or "fused" in names) and not args.parse_model:
        return "--parse-model is required when parsed_axis_match or fused is a pool source"
    if args.random < 0:
        return "--random cannot be negative"
    if args.per_query - args.random < 1:
        return "--per-query must be larger than --random"
    if args.depth < 1:
        return "--depth must be at least 1"
    return None


def require_readonly() -> None:
    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        sys.exit("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly) and APP_ENV must be set.")


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


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
    stats = import_judgment_files([Path(name) for name in args.files], gold, allow_overwrite=True)
    print(f"added {stats['added']}, overwritten {stats['overwritten']}, unchanged {stats['unchanged']}; "
          f"{stats['total']} judgments in total")
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


def _build_pipeline(parse_model: str):
    """The production pipeline for evaluation: the tracked parse.* and search.meme_from_text.* settings with the
    parser model taken from the command line."""
    from config.settings import settings
    from parse.config import load_config as load_parse_config
    from parse.parser import QueryParser
    from retrieval.config import load_config
    from retrieval.embed import OllamaEmbedClient
    from retrieval.pipeline import MemeFromTextPipeline
    from retrieval.similarity import load_similarity

    def get(key, default=None):
        return parse_model if key == "parse.model" else settings.get(key, default)

    parser = QueryParser(load_parse_config(get))
    config = load_config(settings.get)
    from Storage.models import PROFILE_EMBEDDING_DIM
    return MemeFromTextPipeline(config, parser, OllamaEmbedClient(config.ollama_url, PROFILE_EMBEDDING_DIM),
                                load_similarity(),
                                max_input_chars=int(settings.get("parse.max_input_chars")))


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
    if name == "fused":
        from evaluation.retrievers.fused import FusedRetriever
        return FusedRetriever(session, _build_pipeline(parse_model))
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
    error = validate_pool_args(args)
    if error:
        return _error(error)
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
    names = pool_source_names(args.sources)
    async with AsyncSessionLocal() as session:
        await _check_corpus(session, corpus, check_digest=bool({"situation_embedding", "fused"} & set(names)))
        retrievers = {name: _make_retriever(name, session, args.seed, args.parse_model) for name in names}
        randomiser = RandomRetriever(args.seed)
        for retriever in retrievers.values():
            if hasattr(retriever, "prepare"):
                await retriever.prepare(queries)
        for query in queries:
            ranked = {name: [image_id for image_id, _ in await retriever.retrieve(query, corpus_ids, args.depth)]
                      for name, retriever in retrievers.items()}
            random_ids = [image_id for image_id, _ in await randomiser.retrieve(query, corpus_ids, 50)]
            ranked_target = args.per_query - args.random
            pools[query.id] = build_pool(query.id, ranked, random_ids, exclude=set(judged.get(query.id, {})),
                                         ranked_target=ranked_target, random_target=args.random, seed=args.seed)
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
    if args.retriever in ("parsed_axis_match", "fused") and not args.parse_model:
        return _error("--parse-model is required for the parsed_axis_match and fused retrievers")
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
        state = await _check_corpus(session, corpus, check_digest=args.retriever in ("situation_embedding", "fused"))
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
    latency = None
    if args.retriever == "fused":
        from evaluation.runner import format_latency, latency_summary
        latency = latency_summary(retriever.query_meta)
        baseline["latency"] = latency
    target.parent.mkdir(parents=True, exist_ok=True)
    save_baseline(target, baseline)
    bias = None
    from evaluation.sheet import load_pool_sources
    sources = load_pool_sources(args.pools_dir)
    if sources:
        bias = pooling_bias(results, sources)
    print(format_report(baseline, bias))
    if latency is not None:
        print(format_latency(latency))
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


async def cmd_tune_weights(args, gold: Path) -> int:
    """Collect the candidates of every reviewed query once (live Ollama and the read-only database), then tune
    the six weights with cross-validation. Prints the recommended settings; committing them is a human step."""
    target = gold / "tuning" / f"{args.label}.json"
    if target.exists() and not args.force:
        return _error(f"{target} already exists (use --force to replace it)")
    if args.folds < 2:
        return _error("--folds must be at least 2")
    require_readonly()
    import json
    import uuid
    from evaluation.tuning import PreparedQuery, split_degraded, tune
    from repository.meme_retrieval import MemeRetrievalRepository
    from retrieval.pipeline import SearchRequest
    from retrieval.similarity import load_similarity
    from Storage.db import AsyncSessionLocal
    corpus = load_corpus(gold / "corpus.json")
    queries = [q for q in load_queries(gold / "queries.json") if q.reviewed]
    judged = judgments_by_query(load_judgments(gold / "judgments.json"))
    corpus_ids = frozenset(uuid.UUID(image_id) for image_id in corpus.image_ids())
    pipeline = _build_pipeline(args.parse_model)
    prepared = []
    async with AsyncSessionLocal() as session:
        await _check_corpus(session, corpus, check_digest=True)
        repository = MemeRetrievalRepository(session)
        await pipeline.warm_up_models()
        for query in queries:
            item = await pipeline.prepare(SearchRequest(text=query.text, language_hint=query.language,
                                                        restrict_ids=corpus_ids), repository)
            prepared.append(PreparedQuery(query.id, query.language, item.view, tuple(item.candidates),
                                          item.lexical_active, judged.get(query.id, {}), item.degraded,
                                          item.fallback))
            if "breaker_open" in item.fallback:
                return _error("the parser circuit breaker opened during collection; the remaining queries would "
                              "all degrade. Fix the parser model or Ollama and run again (nothing was written)")
    prepared, degraded = split_degraded(prepared)
    if degraded:
        listing = ", ".join(f"{item.query_id}: {item.fallback}" for item in degraded)
        if not args.allow_degraded:
            return _error(f"{len(degraded)} of {len(degraded) + len(prepared)} queries degraded ({listing}); tuning "
                          "on degraded profiles would corrupt the weights. Fix the parser or pass --allow-degraded "
                          "to exclude them (nothing was written)")
        _warn(f"excluding {len(degraded)} degraded queries from the tuning: {listing}")
    if not prepared:
        return _error("no clean query is left to tune on (nothing was written)")
    report = tune(prepared, pipeline.config, load_similarity(), folds=args.folds, seed=args.seed)
    report.update({"label": args.label, "date": date_class.today().isoformat(), "parseModel": args.parse_model,
                   "queries": len(prepared),
                   "degradedQueries": [{"queryId": item.query_id, "fallback": item.fallback} for item in degraded],
                   "queriesSha": file_sha256(gold / "queries.json"),
                   "judgmentsSha": file_sha256(gold / "judgments.json")})
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    weights = report["recommendedWeights"]
    print(f"default weights nDCG@10 {report['defaultScore']:.3f}; tuned in-sample {report['inSampleScore']:.3f}; "
          f"cross-validated {report['cvScore']:.3f} (folds {', '.join(f'{s:.3f}' for s in report['foldScores'])}, "
          f"spread {report['foldSpread']:.3f})")
    print("settings.yaml snippet:\n  weights: {" + ", ".join(f"{name}: {weights[name]}" for name in weights) + "}")
    print(f"wrote {target}")
    return 0


async def cmd_export_ratings(args, gold: Path) -> int:
    """Turn the owner's ratings into review files (never an automatic merge into the frozen gold set)."""
    require_readonly()
    out = Path(args.out)
    refusal = prepare_output_dir(out, args.force)
    if refusal:
        return _error(refusal)
    from config.settings import settings
    from Storage.db import AsyncSessionLocal
    max_chars = int(settings.get("parse.max_input_chars", 600))
    async with AsyncSessionLocal() as session:
        stats = await export_ratings(session, gold, out, round_number=args.round, max_chars=max_chars,
                                     today=date_class.today().isoformat())
    print(f"{stats['ratings']} ratings: {stats['importable']} importable judgments in {stats['files']} file(s), "
          f"{stats['already_judged']} already judged, {stats['outside_corpus']} outside the corpus, "
          f"{stats['new_messages']} new message(s); wrote {out}")
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
    pools.add_argument("--sources", default=DEFAULT_POOL_SOURCES,
                       help="comma-separated pool sources: " + ", ".join(POOL_SOURCE_CHOICES))
    pools.add_argument("--parse-model", default=None, help="model for the parsed_axis_match pool source")
    pools.add_argument("--random", type=int, default=3, help="number of random candidates per query")
    pools.add_argument("--depth", type=int, default=POOL_DEPTH, help="ranks of each source that are considered")

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

    tune_cmd = commands.add_parser("tune-weights")
    tune_cmd.add_argument("--parse-model", required=True)
    tune_cmd.add_argument("--label", required=True)
    tune_cmd.add_argument("--folds", type=int, default=5)
    tune_cmd.add_argument("--seed", type=int, default=7)
    tune_cmd.add_argument("--force", action="store_true")
    tune_cmd.add_argument("--allow-degraded", action="store_true",
                          help="exclude queries whose profile degraded (parser failure, open breaker) instead of "
                               "refusing to tune")

    export = commands.add_parser("export-ratings")
    export.add_argument("--round", type=int, required=True)
    export.add_argument("--out", default=str(REPO_ROOT / DEFAULT_WORK / "ratings-export"),
                        help="default: <repository>/evaluation/work/ratings-export")
    export.add_argument("--force", action="store_true")

    commands.add_parser("list-baselines")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    gold = Path(args.gold_dir)
    handlers = {"import-judgments": cmd_import_judgments, "compare": cmd_compare, "list-baselines": cmd_list_baselines,
                "sheet": cmd_sheet}
    async_handlers = {"build-corpus": cmd_build_corpus, "build-pools": cmd_build_pools, "run": cmd_run,
                      "parse-eval": cmd_parse_eval, "tune-weights": cmd_tune_weights,
                      "export-ratings": cmd_export_ratings}
    try:
        if args.command in handlers:
            return handlers[args.command](args, gold)
        return asyncio.run(async_handlers[args.command](args, gold))
    except (GoldError, CompareError) as error:
        return _error(str(error))


if __name__ == "__main__":
    sys.exit(main())
