import json, time
from pathlib import Path
from batch.tools.recut_spike.vlm import ground
items = json.loads(Path("evaluation/work/recut_spike/sample.json").read_text(encoding="utf-8"))["items"][:12]
for it in items:
    t = time.time(); r = ground(it["path"])
    print(round(time.time() - t, 1), it["w"], it["h"], r and (r["box"], r["raw"][:60]))
