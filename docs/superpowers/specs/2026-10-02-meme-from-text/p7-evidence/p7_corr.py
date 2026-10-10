"""P7 memo evidence, step 3: how much of the ranking quality is explained by parse quality (offline)."""
import json
import statistics
import sys
from pathlib import Path

rows = {r["id"]: r for r in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))}
parse = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
ids = [q for q in rows if q in parse and rows[q]["ndcg"] is not None]


def avg(values):
    values = list(values)
    return round(statistics.mean(values), 3) if values else None


print("queries", len(ids), "| mean parse macro-F1", avg(parse[q]["macro"] for q in ids), "| mean nDCG@10", avg(rows[q]["ndcg"] for q in ids))
print("axis F1:", {a: avg(parse[q]["f1"][a] for q in ids) for a in ("emotion", "stance", "topic", "tone")})

order = sorted(ids, key=lambda q: parse[q]["macro"])
third = len(order) // 3
for name, group in (("worst third by parse", order[:third]), ("middle third", order[third:2 * third]), ("best third by parse", order[2 * third:])):
    print(f"  {name:22s} n={len(group):2d} parse macro-F1 {avg(parse[q]['macro'] for q in group)}  nDCG@10 {avg(rows[q]['ndcg'] for q in group)}  "
          f"oracle(top20) {avg(rows[q]['oracle']['20'] for q in group)}  found-in-top50 {avg(rows[q]['cov']['50'] for q in group)}")


def corr(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return round(num / den, 3) if den else None


print("Pearson r (parse macro-F1 vs nDCG@10):", corr([parse[q]["macro"] for q in ids], [rows[q]["ndcg"] for q in ids]))
print("Pearson r (parse macro-F1 vs share of relevant found in top 50):", corr([parse[q]["macro"] for q in ids], [rows[q]["cov"]["50"] for q in ids]))

print("\nFailure attribution for the weak queries (nDCG@10 < 0.30):")
weak = [q for q in ids if rows[q]["ndcg"] < 0.30]
buckets = {"parse-driven (parse macro-F1 < 0.40)": [], "ranking-driven (parse ok, relevant mostly inside top 50 but not top 10)": [],
           "other (parse ok, relevant mostly outside top 50)": []}
for q in weak:
    if parse[q]["macro"] < 0.40:
        buckets["parse-driven (parse macro-F1 < 0.40)"].append(q)
    elif rows[q]["cov"]["50"] >= 0.5:
        buckets["ranking-driven (parse ok, relevant mostly inside top 50 but not top 10)"].append(q)
    else:
        buckets["other (parse ok, relevant mostly outside top 50)"].append(q)
for name, group in buckets.items():
    print(f"  {name}: {len(group)} {group}")

print("\nWeak queries: text / expected vs parsed")
for q in sorted(weak, key=lambda q: rows[q]["ndcg"]):
    r = rows[q]
    print(f"  {q} {r['lang']} nDCG {r['ndcg']:.2f} parseF1 {parse[q]['macro']:.2f} cov10 {r['cov']['10']:.2f} cov50 {r['cov']['50']:.2f} | parsed {parse[q]['parsed']}")
