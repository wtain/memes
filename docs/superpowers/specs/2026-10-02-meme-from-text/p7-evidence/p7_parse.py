import asyncio, json, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from config.settings import settings
from evaluation.gold import load_queries
from evaluation.parse_scoring import score_query
from parse.config import load_config as load_parse_config
from parse.parser import QueryParser
def get(key, default=None):
    over = {"parse.model": "qwen2.5:7b-instruct", "parse.timeout_seconds": 120, "parse.retry_min_seconds": 5}
    return over[key] if key in over else settings.get(key, default)
async def main():
    parser = QueryParser(load_parse_config(get))
    out = {}
    for q in [q for q in load_queries(Path("evaluation/gold/queries.json")) if q.reviewed]:
        res = await parser.parse(q.text, q.language)
        profile = res.profile
        meta = {"fallback": res.meta.fallback, "latencyMs": res.meta.latency_ms}
        s = score_query(q.id, q.language, q.expected, profile, meta)
        f1 = {a: v.f1 for a, v in s.axes.items()}
        out[q.id] = {"f1": f1, "macro": sum(f1.values()) / len(f1), "detected": profile.get("language"),
                     "parsed": {a: [v["key"] for v in profile.get(a, [])] for a in ("emotion", "stance", "topic", "tone")},
                     "situation": profile.get("situation"), "degraded": bool(profile.get("degraded"))}
    Path(sys.argv[2]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(len(out), "parsed; mean macro F1", round(sum(v["macro"] for v in out.values()) / len(out), 3),
          "language ok", sum(1 for q, v in out.items() if v["detected"]), "degraded", sum(v["degraded"] for v in out.values()))
asyncio.run(main())
