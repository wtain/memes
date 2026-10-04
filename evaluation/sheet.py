"""The blinded judging sheet and the local pools file (P6 spec, decision 6).

The sheet shows the incoming message and, for each candidate, only the meme image and three grades. It never
contains a retriever name, a profile value or an input text, so judging is not biased by the system's own labels.
"""
import base64
import html
import io
import json
from typing import Mapping, Sequence

from evaluation.gold import GoldQuery
from evaluation.pooling import Pool

GRADE_LABELS = ((2, "2 great reply"), (1, "1 acceptable"), (0, "0 wrong"))
RUBRIC = ("Judge only: is this a good meme to send back as a reply to the message? The grade is about fit, "
          "not about how funny or well made the meme is. 2 = great reply (you would send it), "
          "1 = acceptable (works, not ideal), 0 = wrong (does not fit, or you cannot tell what it means). "
          "Leave a meme ungraded to skip it. Ambiguous messages accept several kinds of reply.")

SCRIPT = """
const META = JSON.parse(document.getElementById('meta').textContent);
const KEY = 'eval-judging-' + META.language + '-r' + META.round;
function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
function save(state) { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* storage unavailable */ } }
const state = load();
function count() { document.getElementById('count').textContent = Object.keys(state).length; }
document.querySelectorAll('input[type=radio]').forEach(function (r) {
  const key = JSON.stringify([r.dataset.q, r.dataset.i]);
  if (state[key] === r.value) { r.checked = true; }
  r.addEventListener('change', function () { state[key] = r.value; save(state); count(); });
});
count();
function exportJudgments() {
  const judgments = Object.keys(state).map(function (key) {
    const pair = JSON.parse(key);
    return {queryId: pair[0], imageId: pair[1], grade: Number(state[key])};
  });
  const payload = {language: META.language, rater: META.rater, date: META.date, round: META.round, judgments: judgments};
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
  link.download = 'judgments-' + META.language + '.json';
  link.click();
}
"""


def render_judging_sheet(*, language: str, queries: Sequence[GoldQuery], pools: Mapping[str, Sequence[str]],
                         thumbnails: Mapping[str, str | None], rater: str, date: str, round_number: int) -> str:
    blocks = []
    shown = [query for query in queries if query.language == language and query.id in pools]
    for query in shown:
        candidates = []
        for image_id in pools[query.id]:
            thumbnail = thumbnails.get(image_id)
            picture = f"<img src='{html.escape(thumbnail, quote=True)}' alt=''>" if thumbnail else "<p>(no image)</p>"
            radios = "".join(
                f"<label><input type='radio' name='{html.escape(query.id + '__' + image_id, quote=True)}' "
                f"value='{grade}' data-q='{html.escape(query.id, quote=True)}' "
                f"data-i='{html.escape(image_id, quote=True)}'> {label}</label> "
                for grade, label in GRADE_LABELS)
            candidates.append(f"<div class='cand'>{picture}<p>{radios}</p></div>")
        blocks.append(f"<section><h3>{html.escape(query.id)}</h3>"
                      f"<blockquote>{html.escape(query.text)}</blockquote>"
                      f"<div class='row'>{''.join(candidates)}</div></section>")
    meta = json.dumps({"language": language, "rater": rater, "date": date, "round": round_number},
                      ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Judging sheet</title>"
        "<style>body{font-family:sans-serif;max-width:1300px;margin:1rem auto}section{border-bottom:1px solid #999;"
        "padding:1rem 0}blockquote{font-size:1.2rem;background:#f4f4f4;padding:.6rem;margin:.5rem 0}"
        ".row{display:flex;gap:1rem;flex-wrap:wrap}.cand{width:260px}img{max-width:260px;max-height:260px}</style>"
        f"</head><body><h1>Judging sheet ({html.escape(language)})</h1><p>{html.escape(RUBRIC)}</p>"
        f"<p>Graded so far: <b id='count'>0</b> of {sum(len(pools[q.id]) for q in shown)}. "
        "Your progress is saved in this browser; you can close the page and come back.</p>"
        + "".join(blocks)
        + f"<button onclick='exportJudgments()'>Download judgments-{html.escape(language)}.json</button>"
        f"<script type='application/json' id='meta'>{meta}</script><script>{SCRIPT}</script></body></html>"
    )


def make_thumbnail(path: str, size: int = 400) -> str | None:
    """A base64 JPEG data URI, or None when the file cannot be read (Pillow is imported lazily)."""
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.thumbnail((size, size))
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception:
        return None


def pools_to_dict(round_number: int, per_query: int, pools: Mapping[str, Pool], filenames: Mapping[str, str]) -> dict:
    return {
        "round": round_number,
        "perQuery": per_query,
        "pools": {
            query_id: {
                "candidates": list(pool.candidates),
                "sources": {image_id: list(sources) for image_id, sources in pool.sources.items()},
                "filenames": {image_id: filenames[image_id] for image_id in pool.candidates if image_id in filenames},
            }
            for query_id, pool in pools.items()
        },
    }


def pools_from_dict(data: Mapping) -> tuple[dict[str, Pool], dict[str, str]]:
    pools, filenames = {}, {}
    for query_id, entry in data["pools"].items():
        pools[query_id] = Pool(query_id, tuple(entry["candidates"]),
                               {image_id: tuple(sources) for image_id, sources in entry["sources"].items()})
        filenames.update(entry.get("filenames", {}))
    return pools, filenames
