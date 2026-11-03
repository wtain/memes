const SOURCE_LABELS: Record<string, string> = {
  own: "The image itself",
  hash_in_batch: "Identical files dropped together",
  hash_cross_corpus: "Identical files dropped again",
  ingest_tier_a: "Ingestion review, close match",
  ingest_tier_b: "Ingestion review, looser match",
  cluster_review: "Duplicates page",
  agent_review: "Duplicate review command",
  file_duplicates: "Identical files found in the collection",
  adjustment: "Manual adjustment",
  comics_build: "Comics built",
}

/** Count as shown in the breakdown: only the adjustment is signed (+28, -4); the other sources are never negative. */
export function formatSourceCount(source: string, count: number): string {
  return source === "adjustment" && count > 0 ? `+${count}` : String(count)
}

/** Label for a breakdown source; an unknown key (a source added later) shows as is. */
export function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}
