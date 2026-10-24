"""Resumable: B = VLM grounding box; C gate = VLM 'has extras' on the original and on A's crop. Budget-limited."""
import json, sys, time
from pathlib import Path
from PIL import Image
from batch.tools.recut_spike.vlm import ground, has_extras

OUT = Path("evaluation/work/recut_spike")
N, BUDGET = int(sys.argv[1]), float(sys.argv[2])
items = json.loads((OUT / "sample.json").read_text(encoding="utf-8"))["items"][:N]
a = json.loads((OUT / "result_a.json").read_text(encoding="utf-8"))
done = set()
f = OUT / "result_bc.jsonl"
if f.exists():
    done = {json.loads(l)["id"] for l in f.read_text(encoding="utf-8").splitlines() if l}
t0 = time.time()
with f.open("a", encoding="utf-8") as out:
    for it in items:
        if it["id"] in done:
            continue
        if time.time() - t0 > BUDGET:
            break
        rec = {"id": it["id"]}
        try:
            rec["b"] = ground(it["path"])
            pa = a.get(it["id"])
            if pa and "box" in pa:
                with Image.open(it["path"]) as im:
                    im = im.convert("RGB")
                    rec["gate_orig"] = has_extras(im)
                    rec["gate_crop"] = has_extras(im.crop(tuple(pa["box"])))
        except Exception as e:
            rec["error"] = str(e)[:100]
        out.write(json.dumps(rec) + "\n"); out.flush()
print("done so far", len(done), "budget used", round(time.time() - t0))
