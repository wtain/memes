"""Read-only: list active images (not in the frozen evaluation corpus) with file paths. Uses DATABASE_URL_READONLY only."""
import asyncio, json, random, re, sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "evaluation/work/recut_spike"


def env(name: str) -> str:
    for line in (ROOT / "environments/.env.general").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"{name} missing")


async def main(n: int) -> None:
    dsn = env("DATABASE_URL_READONLY").replace("postgresql+asyncpg://", "postgresql://")
    base = Path(env("BASE_PATH"))
    corpus = {i["imageId"] for i in json.loads((ROOT / "evaluation/gold/corpus.json").read_text(encoding="utf-8"))["images"]}
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("select id::text, filename, width, height from images where status='active' order by id")
    finally:
        await conn.close()
    rows = [r for r in rows if r["id"] not in corpus and (base / r["filename"]).is_file()]
    random.Random(11).shuffle(rows)
    items = [{"id": r["id"], "path": str(base / r["filename"]), "w": r["width"], "h": r["height"]} for r in rows[:n]]
    (OUT / "sample.json").write_text(json.dumps({"total_eligible": len(rows), "items": items}), encoding="utf-8")
    print("eligible", len(rows), "sampled", len(items))


asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3000))
