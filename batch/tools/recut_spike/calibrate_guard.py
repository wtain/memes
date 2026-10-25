"""THROWAWAY. For each judged margin-kind box, the largest share of any OCR region lying outside the box, and how a
threshold sweep would have treated each grade. Read-only (DATABASE_URL_READONLY)."""
import asyncio
import json
from pathlib import Path

import asyncpg

from batch.utils.crop_detection import region_rect

ROOT = Path(__file__).resolve().parents[3]
GOLD = ROOT / "evaluation/gold/recut"
GRID = [0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50]


def env(name: str) -> str:
    for line in (ROOT / "environments/.env.general").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"{name} missing")


def judged():
    items = []
    k1 = json.loads((GOLD / "spike-round1-key.json").read_text(encoding="utf-8"))
    j1 = json.loads((GOLD / "spike-round1-judgments.json").read_text(encoding="utf-8"))
    for k, v in j1.items():
        by = set(k1[k]["by"])
        if k1[k]["box"] and ("A" in by or "C" in by) and v in "012":
            items.append((k1[k]["id"], k1[k]["box"], v))
    k2 = json.loads((GOLD / "spike-round2-key.json").read_text(encoding="utf-8"))
    j2 = json.loads((GOLD / "spike-round2-judgments.json").read_text(encoding="utf-8"))
    for k, v in j2.items():
        if k2[k]["band"].startswith("margin") and v in "012":
            items.append((k2[k]["id"], k2[k]["box"], v))
    return items


def worst_outside(box, regions):
    bx0, by0, bx1, by1 = box
    worst = 0.0
    for raw in regions:
        r = region_rect(raw)
        if r is None or (r[2] - r[0]) * (r[3] - r[1]) <= 0:
            continue
        x0, y0, x1, y1 = r
        area = (x1 - x0) * (y1 - y0)
        inter = max(0.0, min(x1, bx1) - max(x0, bx0)) * max(0.0, min(y1, by1) - max(y0, by0))
        worst = max(worst, (area - inter) / area)
    return worst


async def main():
    items = judged()
    dsn = env("DATABASE_URL_READONLY").replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "select image_id::text as id, bbox from ocr_texts where image_id = any($1::uuid[]) "
            "and confidence >= 0.4 and bbox is not null",
            [i for i, _, _ in items])
    finally:
        await conn.close()
    regions = {}
    for r in rows:
        regions.setdefault(r["id"], []).append(json.loads(r["bbox"]) if isinstance(r["bbox"], str) else r["bbox"])
    scored = [(worst_outside(box, regions.get(i, [])), g) for i, box, g in items]
    n = {g: sum(1 for _, x in scored if x == g) for g in "012"}
    print("judged margin boxes:", n, "| images with OCR regions:", len(regions), "of", len({i for i, _, _ in items}))
    print("threshold | dropped grade 2 | dropped grade 1 | dropped grade 0")
    for t in GRID:
        d = {g: sum(1 for w, x in scored if x == g and w > t) for g in "012"}
        print(f"{t:>9} | {d['2']}/{n['2']} | {d['1']}/{n['1']} | {d['0']}/{n['0']}")
    bad = n["1"] + n["0"]
    ok = [t for t in GRID if sum(1 for w, x in scored if x in "01" and w > t) >= bad / 2
          and sum(1 for w, x in scored if x == "2" and w > t) <= 0.05 * n["2"]]
    print("decision: keep guard at", max(ok) if ok else "NONE (switch off with 1.0)")


asyncio.run(main())
