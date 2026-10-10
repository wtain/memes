"""P7 memo evidence, step 4: a pointwise LLM re-rank probe on the top-N of the production ranking (read-only DB, local Ollama).
Scored on JUDGED items only (unjudged candidates are dropped from both lists) so the pooling bias cannot favour either side."""
import asyncio
import json
import statistics
import sys
import urllib.request
import uuid
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from evaluation.__main__ import require_readonly
from evaluation.gold import load_judgments, load_queries
from evaluation.metrics import query_metrics
from Storage.db import AsyncSessionLocal
from Storage.models import MemeProfile, MemeProfileValue

RAW = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
OUT = Path(sys.argv[2])
TOP_N = int(sys.argv[3])
PER_LANGUAGE = int(sys.argv[4])
MODEL = "qwen2.5:7b-instruct"
OLLAMA = "http://127.0.0.1:11434"
GOLD = Path("evaluation/gold")

PROMPT = """You help someone pick a reaction meme to reply to a message.
Message ({language}): {text}

Candidate meme:
- situation it fits: {situation}
- use it when: {use_when}
- emotion: {emotion}; stance: {stance}; topic: {topic}; tone: {tone}

Would this meme be a good reply to the message? Answer with a single digit:
0 = not a fit, 1 = acceptable, 2 = great fit."""


def ask(prompt: str) -> int | None:
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "seed": 7, "num_predict": 3, "num_ctx": 2048}}).encode()
    request = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=300) as response:
        text = json.loads(response.read())["response"].strip()
    for char in text:
        if char in "012":
            return int(char)
    return None


async def profiles_for(image_ids):
    async with AsyncSessionLocal() as session:
        ids = [uuid.UUID(i) for i in image_ids]
        rows = (await session.execute(select(MemeProfile).where(MemeProfile.image_id.in_(ids)))).scalars().all()
        values = (await session.execute(select(MemeProfileValue).where(MemeProfileValue.image_id.in_(ids)))).scalars().all()
    by_image = defaultdict(lambda: defaultdict(list))
    for v in values:
        by_image[str(v.image_id)][v.axis].append(v.value_key)
    return {str(r.image_id): {"situation": r.situation_text or "-", "use_when": r.use_when or "-",
                              **{a: ", ".join(by_image[str(r.image_id)][a]) or "-" for a in ("emotion", "stance", "topic", "tone")}}
            for r in rows}


async def main():
    require_readonly()
    queries = {q.id: q for q in load_queries(GOLD / "queries.json") if q.reviewed}
    grades = defaultdict(dict)
    for j in load_judgments(GOLD / "judgments.json"):
        grades[j.query_id][j.image_id] = j.grade
    chosen = []
    for lang in ("en", "ru", "es"):
        chosen += [qid for qid in sorted(queries) if queries[qid].language == lang][:PER_LANGUAGE]
    results = {}
    for index, qid in enumerate(chosen, 1):
        ranked = [i for i, _ in RAW["rankings"][qid]][:TOP_N]
        profiles = await profiles_for(ranked)
        scores = {}
        for image_id in ranked:
            p = profiles.get(image_id)
            scores[image_id] = ask(PROMPT.format(language=queries[qid].language, text=queries[qid].text, **p)) if p else None
        g = grades[qid]
        base = [i for i in ranked if i in g]                                               # judged only, original order
        reranked = sorted(base, key=lambda i: (-(scores[i] if scores[i] is not None else 1), ranked.index(i)))
        oracle = sorted(base, key=lambda i: (-g[i], ranked.index(i)))
        results[qid] = {"lang": queries[qid].language, "judged_in_top": len(base),
                        "base": query_metrics(base, g).ndcg, "llm": query_metrics(reranked, g).ndcg,
                        "oracle": query_metrics(oracle, g).ndcg,
                        "agree": [(scores[i], g[i]) for i in base if scores[i] is not None]}
        print(f"[{index}/{len(chosen)}] {qid} judged {len(base)} base {results[qid]['base']} llm {results[qid]['llm']}", flush=True)
    OUT.write_text(json.dumps(results), encoding="utf-8")


asyncio.run(main())
