import type { Meme } from "../types/generated/all"
import type { MemeNeighbors } from "./MemeDetailsModal"

/**
 * The quick-look neighbours of `current` in `list`, matched by id. A meme that is not in the list
 * (evicted, or the list was replaced) gets none, so the modal shows no arrows for it.
 * `onNextStep` runs after a step forward, with the index stepped to (never after a step back).
 */
export function neighborsOf(
  list: Meme[],
  current: Meme,
  select: (meme: Meme) => void,
  onNextStep?: (index: number) => void,
): MemeNeighbors | undefined {
  const index = list.findIndex(m => m.id === current.id)
  if (index < 0) return undefined
  const prev = list[index - 1]
  const next = list[index + 1]
  return {
    prev,
    next,
    onPrev: prev ? () => select(prev) : undefined,
    onNext: next ? () => { select(next); onNextStep?.(index + 1) } : undefined,
  }
}
