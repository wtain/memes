"""
Fetch one duplicate cluster's data for agent reasoning.

Usage:
    python tools/agent_duplicates.py --env metal [--threshold 0.1] [--cluster_id N]

Without --cluster_id, reads .agent_state/duplicates_{env}.json to find the next
unprocessed cluster and advances the state after printing.

Output: JSON to stdout.
State: .agent_state/duplicates_{env}.json
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Load env file before any Storage imports (Storage.config reads DATABASE_URL at import time)
def _load_env(env: str) -> str:
    root = Path(__file__).parent.parent
    env_file = root / "environments" / f".env.{env}"
    if not env_file.exists():
        sys.exit(f"Env file not found: {env_file}")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
    ports = {"metal": 8081, "general": 8082, "it": 8083}
    return f"http://127.0.0.1:{ports[env]}"


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


async def _fetch_cluster(session, cluster_id: int, threshold: float) -> dict | None:
    from sqlalchemy import select, func, text
    from sqlalchemy.orm import aliased
    from Storage.models import Image, OCRText, OllamaDescription, Embedding, TmpImageClusters, ImageExtras

    img = aliased(Image)
    cluster = aliased(TmpImageClusters)
    extras = aliased(ImageExtras)

    # Get all non-excluded members of this cluster
    rows = await session.execute(
        select(
            img.id,
            img.filename,
            extras.exclude,
        )
        .join(cluster, cluster.image_id == img.id)
        .outerjoin(extras, extras.image_id == img.id)
        .where(cluster.cluster_id == cluster_id)
        .order_by(img.id)
    )
    members_raw = rows.all()
    if not members_raw:
        return None

    member_ids = [str(r.id) for r in members_raw]

    # Fetch OCR text per member
    ocr_rows = await session.execute(
        select(OCRText.image_id, func.string_agg(OCRText.text, " ").label("ocr"))
        .where(OCRText.image_id.in_([r.id for r in members_raw]))
        .group_by(OCRText.image_id)
    )
    ocr_map = {str(r.image_id): r.ocr for r in ocr_rows}

    # Fetch descriptions per member
    desc_rows = await session.execute(
        select(OllamaDescription.image_id, OllamaDescription.text)
        .where(OllamaDescription.image_id.in_([r.id for r in members_raw]))
    )
    desc_map = {str(r.image_id): r.text for r in desc_rows}

    # Fetch embeddings per member
    emb_rows = await session.execute(
        select(Embedding.image_id, Embedding.embedding)
        .where(Embedding.image_id.in_([r.id for r in members_raw]))
    )
    emb_map = {str(r.image_id): r.embedding for r in emb_rows}

    # Compute pairwise cosine distances in-DB via pgvector
    pairwise: dict[str, dict[str, float]] = {mid: {} for mid in member_ids}
    emb_ids = [r.id for r in members_raw if str(r.id) in emb_map]
    for i, id_a in enumerate(emb_ids):
        emb_a = emb_map[str(id_a)]
        for id_b in emb_ids[i + 1 :]:
            emb_b = emb_map[str(id_b)]
            dist_row = await session.execute(
                select(
                    Embedding.embedding.cosine_distance(emb_b)
                ).where(Embedding.image_id == id_a)
            )
            dist = dist_row.scalar()
            pairwise[str(id_a)][str(id_b)] = round(float(dist), 4)
            pairwise[str(id_b)][str(id_a)] = round(float(dist), 4)

    members = []
    for r in members_raw:
        mid = str(r.id)
        members.append({
            "id": mid,
            "filename": r.filename,
            "already_excluded": bool(r.exclude),
            "ocr_text": ocr_map.get(mid, ""),
            "has_description": mid in desc_map,
            "description": desc_map.get(mid, ""),
            "pairwise_distances": pairwise.get(mid, {}),
        })

    return {"cluster_id": cluster_id, "members": members}


async def _next_cluster_id(session, after_cluster_id: int | None) -> int | None:
    from sqlalchemy import select
    from Storage.models import TmpImageClusters

    q = select(TmpImageClusters.cluster_id).distinct().order_by(TmpImageClusters.cluster_id)
    if after_cluster_id is not None:
        q = q.where(TmpImageClusters.cluster_id > after_cluster_id)
    q = q.limit(1)
    row = await session.execute(q)
    return row.scalar()


async def main(env: str, threshold: float, cluster_id: int | None, reset: bool):
    _load_env(env)

    from Storage.db import AsyncSessionLocal

    state = {} if reset else _load_state(env)
    last = state.get("last_processed_cluster_id")

    async with AsyncSessionLocal() as session:
        if cluster_id is None:
            cluster_id = await _next_cluster_id(session, last)
            if cluster_id is None:
                print(json.dumps({"done": True, "message": "All clusters processed"}))
                return

        data = await _fetch_cluster(session, cluster_id, threshold)
        if data is None:
            print(json.dumps({"error": f"Cluster {cluster_id} not found"}), file=sys.stderr)
            sys.exit(1)

    state["last_processed_cluster_id"] = cluster_id
    state["processed_count"] = state.get("processed_count", 0) + 1
    _save_state(env, state)

    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", default="metal", choices=["metal", "general", "it"])
    parser.add_argument("--threshold", type=float, default=0.1)
    parser.add_argument("--cluster_id", type=int, default=None)
    parser.add_argument("--reset", action="store_true", help="Ignore saved state")
    args = parser.parse_args()
    asyncio.run(main(args.env, args.threshold, args.cluster_id, args.reset))