"""P7 memo evidence, step 2: offline headroom analysis of the deep fused rankings (no database, no Ollama)."""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from evaluation.gold import load_judgments, load_queries
from evaluation.metrics import query_metrics

RAW = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
GOLD = Path("evaluation/gold")
queries = {q.id: q for q in load_queries(GOLD / "queries.json") if q.reviewed}
grades = defaultdict(dict)
for j in load_judgments(GOLD / "judgments.json"):
    grades[j.query_id][j.image_id] = j.grade
NS = (10, 20, 30, 50)


def ndcg(ranked, g, k=10):
    return query_metrics(ranked, g, k=k).ndcg


def oracle(ranked, g, n):
    top = ranked[:n]
    ordered = sorted(top, key=lambda image_id: -g.get(image_id, 0))     # stable: ties keep the system order
    return ndcg(ordered, g)


def condensed(ranked, g):
    return [image_id for image_id in ranked if image_id in g]            # drop unjudged: removes the pooling bias


rows = []
for qid, query in queries.items():
    ranked = [image_id for image_id, _ in RAW["rankings"][qid]]
    g = grades[qid]
    positives = {i for i, v in g.items() if v >= 1}
    great = {i for i, v in g.items() if v == 2}
    cond = condensed(ranked, g)
    row = {
        "id": qid, "lang": query.language, "diff": query.difficulty, "length": query.length,
        "n_pos": len(positives), "n_great": len(great), "judged": len(g),
        "ndcg": ndcg(ranked, g),
        "cov": {n: (len(positives & set(ranked[:n])) / len(positives) if positives else None) for n in NS},
        "cov_great": {n: (len(great & set(ranked[:n])) / len(great) if great else None) for n in NS},
        "oracle": {n: oracle(ranked, g, n) for n in NS},
        "cond_base": ndcg(cond, g), "cond_oracle": {n: oracle(cond, g, n) for n in (10, 20, 30)},
        "fallback": RAW["meta"][qid]["fallback"], "candidates": RAW["meta"][qid]["candidates"],
        "first_pos_rank": next((r for r, i in enumerate(ranked, 1) if i in positives), None),
    }
    rows.append(row)


def avg(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else None


def block(title, subset):
    print(f"\n== {title} ({len(subset)} queries)")
    print(f"  nDCG@10 now {avg(r['ndcg'] for r in subset)}   judged-only nDCG@10 now {avg(r['cond_base'] for r in subset)}")
    print("  judged-relevant (grade>=1) found within top N:   " + "   ".join(f"top{n}: {avg(r['cov'][n] for r in subset)}" for n in NS))
    print("  great (grade 2) found within top N:              " + "   ".join(f"top{n}: {avg(r['cov_great'][n] for r in subset)}" for n in NS))
    print("  ORACLE re-rank of top N -> nDCG@10:              " + "   ".join(f"N={n}: {avg(r['oracle'][n] for r in subset)}" for n in NS))
    print("  judged-only ORACLE (top N of judged items):      " + "   ".join(f"N={n}: {avg(r['cond_oracle'][n] for r in subset)}" for n in (10, 20, 30)))


print(f"depth {RAW['depth']} model {RAW['model']}; degraded queries: "
      f"{sum(1 for r in rows if r['fallback'] != 'none')}; candidates median {statistics.median(r['candidates'] for r in rows)}")
block("ALL", rows)
for lang in ("en", "ru", "es"):
    block(f"language {lang}", [r for r in rows if r["lang"] == lang])
for diff in ("easy", "hard", "ambiguous"):
    block(f"difficulty {diff}", [r for r in rows if r["diff"] == diff])

print("\n== Failure classes (nDCG@10 < 0.25)")
weak = [r for r in rows if r["ndcg"] is not None and r["ndcg"] < 0.25]
print(f"  {len(weak)} of {len(rows)} queries")
classes = defaultdict(list)
for r in weak:
    if r["n_pos"] == 0:
        classes["no judged positive"].append(r["id"])
    elif r["cov"][50] == 0:
        classes["no relevant in top 50 (retrieval/parse miss: re-rank cannot help)"].append(r["id"])
    elif r["cov"][10] == 0:
        classes["relevant in 11..50 only (re-rank could help)"].append(r["id"])
    else:
        classes["some relevant in top 10, low gains/ordering"].append(r["id"])
for name, ids in classes.items():
    print(f"  {name}: {len(ids)} {ids}")

print("\n== Weak queries detail")
for r in sorted(weak, key=lambda r: r["ndcg"]):
    q = queries[r["id"]]
    print(f"  {r['id']} {r['lang']} {r['diff']:9s} ndcg {r['ndcg']:.2f} pos {r['n_pos']:2d} great {r['n_great']:2d} "
          f"cov10 {r['cov'][10]:.2f} cov50 {r['cov'][50]:.2f} first_pos_rank {r['first_pos_rank']}  "
          f"profile-situation: {q.expected.situation[:60]!r}")

dist = sorted(r["first_pos_rank"] for r in rows if r["first_pos_rank"])
print(f"\nfirst relevant result rank: median {statistics.median(dist)}, within top1 {sum(1 for d in dist if d == 1)}, top3 {sum(1 for d in dist if d <= 3)}, top10 {sum(1 for d in dist if d <= 10)} of {len(dist)}")
print("positives per query: median", statistics.median(r["n_pos"] for r in rows), "min", min(r["n_pos"] for r in rows), "max", max(r["n_pos"] for r in rows))
Path(sys.argv[2]).write_text(json.dumps(rows), encoding="utf-8")
