import json, sys, time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from batch.tools.recut_spike.classical import detect

OUT = Path("evaluation/work/recut_spike")


def one(item):
    try:
        return item["id"], detect(item["path"])
    except Exception as e:  # unreadable file etc.
        return item["id"], {"error": str(e)[:80]}


if __name__ == "__main__":
    items = json.loads((OUT / "sample.json").read_text(encoding="utf-8"))["items"]
    t = time.time()
    with ProcessPoolExecutor(8) as ex:
        res = dict(ex.map(one, items, chunksize=20))
    (OUT / "result_a.json").write_text(json.dumps(res), encoding="utf-8")
    prop = [v for v in res.values() if v and "box" in v]
    print("images", len(items), "proposals", len(prop), "errors", sum(1 for v in res.values() if v and "error" in v), "secs", round(time.time() - t))
