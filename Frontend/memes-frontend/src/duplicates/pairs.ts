import type { ClusterSimilarityPair } from '../types/generated/all'

export function bestClipDistance(pair: ClusterSimilarityPair): number | undefined {
  const clip = pair.candidates.filter(c => c.source === 'clip').map(c => c.distance)
  return clip.length > 0 ? Math.min(...clip) : undefined
}

function bestAnyDistance(pair: ClusterSimilarityPair): number | undefined {
  const all = pair.candidates.map(c => c.distance)
  return all.length > 0 ? Math.min(...all) : undefined
}

function rank(pair: ClusterSimilarityPair): [number, number] {
  const clip = bestClipDistance(pair)
  if (clip !== undefined) return [0, clip]
  const other = bestAnyDistance(pair)
  if (other !== undefined) return [1, other]
  return [2, 0]
}

export function sortPairs(pairs: ClusterSimilarityPair[]): ClusterSimilarityPair[] {
  return [...pairs].sort((p, q) => {
    const [pg, pd] = rank(p)
    const [qg, qd] = rank(q)
    if (pg !== qg) return pg - qg
    if (pd !== qd) return pd - qd
    return p.image_id1.localeCompare(q.image_id1) || p.image_id2.localeCompare(q.image_id2)
  })
}

export function overlapPercent(overlap: number | null): string | null {
  return overlap === null ? null : `${Math.round(overlap * 100)}%`
}
