"""Pilot for choosing the meme profile extraction model (P2 spec, decision 9). One-off tooling.

    sample : (controller only) stratified fixed-seed sample from a live database, READ-ONLY role
    run    : run candidate models over the sample (no database), two runs each for stability
    sheet  : write a blinded side-by-side rating sheet (HTML) and the label map
    decide : combine results, ratings and labels into per-model stats and a winner

Typical session (PowerShell, from the repo root; DATABASE_URL must be the read-only URL for `sample`):
    python -m batch.tools.meme_profile_pilot sample --out sample.json
    python -m batch.tools.meme_profile_pilot run --sample sample.json --text-models qwen2:latest,qwen2.5vl:7b,<third> --vision-model qwen2.5vl:7b --base-path <BASE_PATH> --out results.json
    python -m batch.tools.meme_profile_pilot sheet --results results.json --base-path <BASE_PATH> --out sheet.html --labels-out labels.json
    python -m batch.tools.meme_profile_pilot decide --results results.json --ratings ratings.json --labels labels.json
"""
import argparse
import asyncio
import base64
import html
import io
import json
import os
import random
import statistics
import sys
import time

RATING_VALUES = {"best": 2, "acceptable": 1, "poor": 0}
AXES = ("emotion", "stance", "topic", "tone")
QUOTAS = {"ocr+description": 20, "ocr only": 10, "description only": 5, "vision": 5}
MIN_VALID_RATE = 0.98


def _stratum(bundle, path: str) -> str:
    if path == "vision":
        return "vision"
    has_description = bool(bundle.descriptions)
    has_ocr = bool(bundle.ocr)
    if has_description and has_ocr:
        return "ocr+description"
    return "description only" if has_description else "ocr only"


def stratify(candidates: list[dict], seed: int, quotas: dict[str, int]) -> list[dict]:
    """Up to quotas[stratum] items per stratum, drawn with a fixed seed; fewer when a stratum is small."""
    rng = random.Random(seed)
    chosen: list[dict] = []
    for stratum, quota in quotas.items():
        pool = sorted((c for c in candidates if c["stratum"] == stratum), key=lambda c: c["image_id"])
        chosen.extend(rng.sample(pool, min(quota, len(pool))))
    return chosen


def shuffled_labels(models: list[str], seed: int, item_id: str) -> dict[str, str]:
    """label -> model, a fixed shuffle per (seed, item) so the sheet is blinded but reproducible."""
    order = sorted(models)
    random.Random(f"{seed}:{item_id}").shuffle(order)
    return {chr(ord("A") + index): model for index, model in enumerate(order)}


def _signature(run: dict):
    profile = run.get("profile") or {}
    return tuple(tuple(sorted(v["key"] for v in profile.get(axis, []))) for axis in AXES)


def summarize(results: list[dict], ratings: dict, labels: dict) -> dict[str, dict]:
    """Per-model valid-output rate, run-to-run stability, mean seconds and mean human rating."""
    models = sorted({model for item in results for model in item["runs"]})
    stats: dict[str, dict] = {}
    for model in models:
        runs = [run for item in results if model in item["runs"] for run in item["runs"][model]]
        valid = sum(1 for run in runs if run["ok"])
        pairs = [item["runs"][model] for item in results if model in item["runs"] and len(item["runs"][model]) >= 2]
        stable = sum(1 for pair in pairs if pair[0]["ok"] and pair[1]["ok"]
                     and _signature(pair[0]) == _signature(pair[1]))
        scores = []
        for item in results:
            for label, labelled_model in labels.get(item["image_id"], {}).items():
                rating = ratings.get(item["image_id"], {}).get(label)
                if labelled_model == model and rating in RATING_VALUES:
                    scores.append(RATING_VALUES[rating])
        stats[model] = {
            "valid_rate": valid / len(runs) if runs else 0.0,
            "stability": stable / len(pairs) if pairs else 0.0,
            "mean_seconds": statistics.mean(run["seconds"] for run in runs) if runs else 0.0,
            "mean_rating": statistics.mean(scores) if scores else None,
            "rated": len(scores),
        }
    return stats


def pick_winner(stats: dict[str, dict], min_valid_rate: float = MIN_VALID_RATE) -> str | None:
    eligible = {model: s for model, s in stats.items()
                if s["valid_rate"] >= min_valid_rate and s["mean_rating"] is not None}
    if not eligible:
        return None
    return min(eligible, key=lambda model: (-eligible[model]["mean_rating"], eligible[model]["mean_seconds"]))


VISION_SUFFIX = "@vision"


def split_winners(stats: dict[str, dict], min_valid_rate: float = MIN_VALID_RATE) -> tuple[str | None, str | None]:
    """(text winner, vision winner without the suffix); vision candidates are keys ending with '@vision'."""
    text = {m: s for m, s in stats.items() if not m.endswith(VISION_SUFFIX)}
    vision = {m: s for m, s in stats.items() if m.endswith(VISION_SUFFIX)}
    vision_winner = pick_winner(vision, min_valid_rate)
    if vision_winner is not None:
        vision_winner = vision_winner[:-len(VISION_SUFFIX)]
    return pick_winner(text, min_valid_rate), vision_winner


def _write_atomic(path: str, text: str) -> None:
    """Write to a temp file in the same directory, then replace the target."""
    directory = os.path.dirname(os.path.abspath(path))
    temp = os.path.join(directory, f".{os.path.basename(path)}.{os.getpid()}.tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp, path)


def _read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _render_output(run: dict) -> str:
    if not run["ok"]:
        # No error text: it can embed the model name and would unblind the sheet (it stays in results.json).
        return "<p class='bad'>invalid output</p>"
    profile = run["profile"]
    lines = [f"<b>situation</b>: {html.escape(str(profile.get('situation') or '-'))}",
             f"<b>useWhen</b>: {html.escape(str(run.get('use_when') or '-'))}"]
    for axis in AXES:
        values = ", ".join(
            f"{html.escape(str(v.get('key', '?')))} ({html.escape(str(v.get('confidence', '-')))})"
            for v in profile.get(axis, [])) or "-"
        lines.append(f"<b>{axis}</b>: {values}")
    return "<br>".join(lines)


def render_sheet(items: list[dict], labels_by_item: dict, thumbnails: dict) -> str:
    """Self-contained HTML: one block per image with the inputs and every model's first run under blinded
    labels, rating radios, and a button that downloads ratings.json ({image_id: {label: rating}})."""
    blocks = []
    for item in items:
        image_id = item["image_id"]
        bundle = item["bundle"]
        inputs = []
        if bundle.get("note"):
            inputs.append(f"<b>Note</b>: {html.escape(bundle['note'])}")
        for key, text in bundle.get("descriptions", []):
            inputs.append(f"<b>Description ({html.escape(key)})</b>: {html.escape(text)}")
        if bundle.get("ocr"):
            inputs.append(f"<b>OCR</b>: {html.escape(bundle['ocr'])}")
        thumbnail = thumbnails.get(image_id)
        picture = f"<img src='{thumbnail}' alt=''>" if thumbnail else "<p>(no image)</p>"
        outputs = []
        for label, model in sorted(labels_by_item[image_id].items()):
            run = item["runs"][model][0]
            radios = "".join(
                f"<label><input type='radio' name='{html.escape(f'{image_id}|{label}')}' "
                f"data-id='{html.escape(image_id)}' data-label='{html.escape(label)}' value='{rating}'> {rating}</label> "
                for rating in RATING_VALUES)
            outputs.append(f"<div class='out'><h4>Output {label}</h4>{_render_output(run)}<p>{radios}</p></div>")
        blocks.append(
            f"<section><h3>{html.escape(item['stratum'])} - {html.escape(item['filename'])}</h3>"
            f"<div class='row'>{picture}<div class='inputs'>{'<br>'.join(inputs) or '(no text inputs)'}</div></div>"
            f"<div class='row'>{''.join(outputs)}</div></section>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Meme profile pilot</title>"
        "<style>body{font-family:sans-serif;max-width:1200px;margin:1rem auto}section{border-bottom:1px solid #999;"
        "padding:1rem 0}.row{display:flex;gap:1rem;flex-wrap:wrap}.out{flex:1;min-width:260px;background:#f4f4f4;"
        "padding:.5rem}.inputs{flex:1}img{max-width:320px;max-height:320px}.bad{color:#b00}</style></head><body>"
        "<h1>Meme profile pilot</h1><p>Rate each output: best / acceptable / poor. Labels are shuffled per image.</p>"
        + "".join(blocks) +
        "<button onclick=\"exportRatings()\">Download ratings.json</button>"
        "<script>function exportRatings(){const out={};document.querySelectorAll('input[type=radio]:checked')"
        ".forEach(r=>{const id=r.dataset.id,label=r.dataset.label;(out[id]=out[id]||{})[label]=r.value});"
        "const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],"
        "{type:'application/json'}));a.download='ratings.json';a.click();}</script></body></html>"
    )


def _thumbnail(path: str) -> str | None:
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.thumbnail((320, 320))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


async def _sample(args) -> None:
    from batch.utils.meme_profile_bundle import build_bundle, choose_path
    from batch.utils.meme_profile_config import load_config
    from config.settings import settings
    from repository.meme_profile_inputs import MemeProfileInputsRepository
    from Storage.db import AsyncSessionLocal

    if "ocr_readonly" not in os.environ.get("DATABASE_URL", ""):
        sys.exit("Refusing to run: DATABASE_URL must be the read-only URL (role ocr_readonly).")
    config = load_config(settings.get)
    if config is None:
        sys.exit("meme_profiles is not configured for this environment (set APP_ENV and run from the repo root)")
    candidates = []
    async with AsyncSessionLocal() as session:
        repo = MemeProfileInputsRepository(session)
        images = await repo.list_active_images()
        for start in range(0, len(images), 1000):
            chunk = images[start:start + 1000]
            raw = await repo.load_inputs([image_id for image_id, _, _ in chunk])
            for image_id, filename, _ in chunk:
                item = raw[image_id]
                bundle = build_bundle(item.note, item.descriptions, item.ocr_rows,
                                      ocr_confidence_min=config.ocr_confidence_min,
                                      ocr_lang_score_min=config.ocr_lang_score_min, limits=config.limits)
                path = choose_path(bundle, config.min_text_chars)
                candidates.append({
                    "image_id": str(image_id), "filename": filename, "path": path, "stratum": _stratum(bundle, path),
                    "bundle": {"note": bundle.note, "descriptions": [list(d) for d in bundle.descriptions],
                               "ocr": bundle.ocr}})
    chosen = stratify(candidates, args.seed, QUOTAS)
    _write_atomic(args.out, json.dumps(chosen, ensure_ascii=False, indent=1))
    print(f"wrote {len(chosen)} item(s) to {args.out}")


def _run_one(extractor, model, bundle, image_path):
    started = time.perf_counter()
    try:
        result = extractor.extract(model=model, note=bundle["note"],
                                   descriptions=[tuple(d) for d in bundle["descriptions"]], ocr=bundle["ocr"],
                                   image_path=image_path)
        return {"ok": True, "error": None, "seconds": time.perf_counter() - started,
                "profile": result.profile, "use_when": result.use_when}
    except Exception as error:
        return {"ok": False, "error": str(error)[:200], "seconds": time.perf_counter() - started,
                "profile": None, "use_when": None}


def _run(args) -> None:
    from ai.meme_profile_extractor import OllamaProfileExtractor
    from taxonomy import load as load_taxonomy

    items = _read_json(args.sample)
    extractor = OllamaProfileExtractor(load_taxonomy(), seed=args.seed, num_ctx=args.num_ctx)
    text_models = [m for m in args.text_models.split(",") if m]
    for index, item in enumerate(items, start=1):
        item["runs"] = {}
        if item["path"] == "text":
            plan = [(model, model, None) for model in text_models]
        else:
            # Own key, so a model that is both a text candidate and the vision model is not mixed across paths.
            plan = [(args.vision_model, f"{args.vision_model}{VISION_SUFFIX}",
                     os.path.join(args.base_path, item["filename"]))]
        for model, key, image_path in plan:
            item["runs"][key] = [_run_one(extractor, model, item["bundle"], image_path) for _ in range(2)]
        print(f"[{index}/{len(items)}] {item['stratum']} {item['filename']}", flush=True)
        _write_atomic(args.out, json.dumps(items, ensure_ascii=False, indent=1))


def _sheet(args) -> None:
    items = _read_json(args.results)
    labels = {item["image_id"]: shuffled_labels(sorted(item["runs"]), args.seed, item["image_id"]) for item in items}
    thumbnails = {item["image_id"]: _thumbnail(os.path.join(args.base_path, item["filename"])) for item in items}
    _write_atomic(args.out, render_sheet(items, labels, thumbnails))
    _write_atomic(args.labels_out, json.dumps(labels, indent=1))
    print(f"wrote {args.out} (open it in a browser) and {args.labels_out} (keep it hidden until rating is done)")


def _decide(args) -> None:
    results = _read_json(args.results)
    ratings = _read_json(args.ratings)
    labels = _read_json(args.labels)
    stats = summarize(results, ratings, labels)
    for model, row in sorted(stats.items()):
        print(f"{model}: {row}")
    text_winner, vision_winner = split_winners(stats)
    print(f"text winner: {text_winner}")
    print(f"vision winner: {vision_winner}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--out", required=True)
    sample.add_argument("--seed", type=int, default=7)
    run = commands.add_parser("run")
    run.add_argument("--sample", required=True)
    run.add_argument("--text-models", required=True)
    run.add_argument("--vision-model", required=True)
    run.add_argument("--base-path", required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--seed", type=int, default=42)
    run.add_argument("--num-ctx", type=int, default=8192)
    sheet = commands.add_parser("sheet")
    sheet.add_argument("--results", required=True)
    sheet.add_argument("--base-path", required=True)
    sheet.add_argument("--out", required=True)
    sheet.add_argument("--labels-out", required=True)
    sheet.add_argument("--seed", type=int, default=7)
    decide = commands.add_parser("decide")
    decide.add_argument("--results", required=True)
    decide.add_argument("--ratings", required=True)
    decide.add_argument("--labels", required=True)
    args = parser.parse_args(argv)

    if args.command == "sample":
        asyncio.run(_sample(args))
    elif args.command == "run":
        _run(args)
    elif args.command == "sheet":
        _sheet(args)
    else:
        _decide(args)


if __name__ == "__main__":
    main()
