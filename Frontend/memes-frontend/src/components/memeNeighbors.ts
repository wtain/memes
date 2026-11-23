import type { Meme } from "../types/generated/all"
import type { MemeNeighbors } from "./MemeDetailsModal"

/**
 * The quick-look neighbours of `current` in `list`, matched by id. A meme that is not in the list
 * (evicted, or the list was replaced) gets none, so the modal shows no arrows for it.
 * `onStep` runs after a step with the index stepped to.
 */
export function neighborsOf(
  list: Meme[],
  current: Meme,
  select: (meme: Meme) => void,
  onStep?: (index: number) => void,
): MemeNeighbors | undefined {
  const index = list.findIndex(m => m.id === current.id)
  if (index < 0) return undefined
  const prev = list[index - 1]
  const next = list[index + 1]
  return {
    prev,
    next,
    onPrev: prev ? () => { select(prev); onStep?.(index - 1) } : undefined,
    onNext: next ? () => { select(next); onStep?.(index + 1) } : undefined,
  }
}
