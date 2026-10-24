import json, collections
from pathlib import Path
O = Path("evaluation/work/recut_spike")
j = json.loads((O / "judgments.json").read_text(encoding="utf-8"))
key = json.loads((O / "sheet_key.json").read_text(encoding="utf-8"))
a = json.loads((O / "result_a.json").read_text(encoding="utf-8"))
print("answered", len(j), "of", len(key))
per = collections.defaultdict(collections.Counter)
for k, v in j.items():
    for ap in key[k]["by"]:
        per[ap][v] += 1
for ap in ["A", "B", "C", "none"]:
    c = per[ap]; g = c["2"] + c["1"] + c["0"]
    print(ap, dict(c), "n_graded", g, "pct2 %.0f%%" % (100 * c["2"] / g) if g else "", "pct1or2 %.0f%%" % (100 * (c["2"] + c["1"]) / g) if g else "")
# A by kind
kc = collections.defaultdict(collections.Counter)
for k, v in j.items():
    i = key[k]["id"]
    if "A" in key[k]["by"] and a.get(i) and "kind" in a[i]: kc[a[i]["kind"]][v] += 1
print("A by kind", {k: dict(v) for k, v in kc.items()})
shared = collections.Counter()
for k, v in j.items():
    shared[tuple(key[k]["by"]), v] += 1
for x in sorted(shared.items()): print(x)
