import json
from pathlib import Path
O = Path("evaluation/work/recut_spike")
s = {i["id"]: i for i in json.loads((O / "sample.json").read_text(encoding="utf-8"))["items"]}
a = json.loads((O / "result_a.json").read_text(encoding="utf-8"))
bc = [json.loads(l) for l in (O / "result_bc.jsonl").read_text(encoding="utf-8").splitlines() if l]
def ratio(box, size): return (box[2]-box[0])*(box[3]-box[1])/(size[0]*size[1])
n=len(bc); print("evaluated", n, "errors", sum("error" in r for r in bc))
ap=[r for r in bc if a.get(r["id"]) and "box" in a[r["id"]]]
print("A proposals in subsample", len(ap))
bp=[r for r in bc if r.get("b") and ratio(r["b"]["box"], r["b"]["size"])<0.92]
print("B boxes removing >=8%", len(bp))
c=[r for r in ap if r.get("gate_orig") and r.get("gate_crop") is False]
print("gate orig extras", sum(1 for r in ap if r.get("gate_orig")), "C pass (extras->clean)", len(c))
