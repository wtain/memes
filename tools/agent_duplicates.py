"""
Fetch one duplicate cluster's data for agent reasoning.

Usage:
    python tools/agent_duplicates.py --env metal [--threshold 0.1] [--cluster_id N]

Without --cluster_id, reads .agent_state/duplicates_{env}.json to find the next
unprocessed cluster (skipping singletons, ordered by cluster_id) and advances
state after printing.

Output: JSON to stdout.
State: .agent_state/duplicates_{env}.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _load_env(env: str):
    root = Path(__file__).parent.parent
    env_file = root / "environments" / f".env.{env}"
    if not env_file.exists():
        sys.exit(f"Env file not found: {env_file}")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _state_path(env: str) -> Path:
    return Path(__file__).parent.parent / ".agent_state" / f"duplicates_{env}.json"


def _load_state(env: str) -> dict:
    p = _state_path(env)
    if p.exists():
        return json.loads(p.read_text())
    return {"last_processed_cluster_id": None, "processed_count": 0}


def _save_state(env: str, state: dict):
    p = _state_path(env)
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


async def main(env: str, cluster_id_arg: int | None, reset: bool):
    _load_env(env)

    from sqlalchemy import select
    from Storage.db import AsyncSessionLocal
    from Storage.models import ImageDescription, Embedding
    from Backend.app.repositories.image_repository import ImageRepository

    state = {} if reset else _load_state(env)
    last = state.get("last_processed_cluster_id")

    async with AsyncSessionLocal() as session:
        repo = ImageRepository(session)

        # Use get_duplicates_clustered to discover clusters — this reuses the singleton
        # filter (HAVING COUNT(*) > 1) and the cluster_id ordering from the backend.
        # limit=200 is large enough to guarantee at least one complete cluster is returned.
        if cluster_id_arg is not None:
            # Fetch starting just before the requested cluster so it's included.
            rows = await repo.get_duplicates_clustered(
                after_cluster_id=cluster_id_arg - 1, limit=200
            )
            cluster_id = cluster_id_arg
        else:
            rows = await repo.get_duplicates_clustered(
                after_cluster_id=last, limit=200
            )
            if not rows:
                print(json.dumps({"done": True, "message": "All clusters processed"}))
                return
            cluster_id = rows[0][3]  # first cluster_id in the ordered result

        members_rows = [(id_, fn, ca, cid, exc) for (id_, fn, ca, cid, exc) in rows
                        if cid == cluster_id]
        if not members_rows:
            print(json.dumps({"error": f"Cluster {cluster_id} not found"}), file=sys.stderr)
            sys.exit(1)

        image_ids = {r[0] for r in members_rows}  # UUID objects for DB queries

        # OCR text — reuse repo.get_texts; min_confidence=0.0 to include all blocks
        ocr_rows = await repo.get_texts(image_ids, min_confidence=0.0)
        ocr_map: dict[str, str] = {}
        for row in ocr_rows:
            mid = str(row.image_id)
            ocr_map[mid] = (ocr_map.get(mid, "") + " " + row.text).strip()

        # Descriptions — no repo method, single direct query
        desc_rows = await session.execute(
            select(ImageDescription.image_id, ImageDescription.text)
            .where(ImageDescription.image_id.in_(image_ids))
        )
        desc_map = {str(r.image_id): r.text for r in desc_rows}

        # Embeddings — reuse repo.get_embedding per member
        emb_map: dict[str, object] = {}
        for r in members_rows:
            emb = await repo.get_embedding(str(r[0]))
            if emb is not None:
                emb_map[str(r[0])] = emb

        # Pairwise cosine distances via pgvector — agent-specific, no backend equivalent
        pairwise: dict[str, dict[str, float]] = {str(r[0]): {} for r in members_rows}
        emb_ids = list(emb_map.keys())
        for i, id_a in enumerate(emb_ids):
            for id_b in emb_ids[i + 1:]:
                dist_row = await session.execute(
                    select(Embedding.embedding.cosine_distance(emb_map[id_b]))
                    .where(Embedding.image_id == id_a)
                )
                dist = dist_row.scalar()
                pairwise[id_a][id_b] = round(float(dist), 4)
                pairwise[id_b][id_a] = round(float(dist), 4)

    members = [
        {
            "id": str(r[0]),
            "filename": r[1],
            "already_flagged": bool(r[4]),
            "ocr_text": ocr_map.get(str(r[0]), ""),
            "has_description": str(r[0]) in desc_map,
            "description": desc_map.get(str(r[0]), ""),
            "pairwise_distances": pairwise.get(str(r[0]), {}),
        }
        for r in members_rows
    ]

    state["last_processed_cluster_id"] = cluster_id
    state["processed_count"] = state.get("processed_count", 0) + 1
    _save_state(env, state)

    print(json.dumps({"cluster_id": cluster_id, "members": members},
                     indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="metal", choices=["metal", "general", "it"])
    parser.add_argument("--cluster_id", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="Ignore saved state")
    args = parser.parse_args()
    asyncio.run(main(args.env, args.cluster_id, args.reset))