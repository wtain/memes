import type { Meme } from "../types/generated/all"

export type AddabilityItem = {
  imageId: string
  addable: boolean
  reason: string | null
  comicsId: string | null
  comicsName: string | null
}

/** The search column's state; `key` ties the results to the query that produced them. */
export type SearchState = {
  key: string | null
  items: Meme[]
  nextCursor: string | null
  hasNext: boolean
  failed: boolean
  addability: Record<string, AddabilityItem>
}

export type SearchAction =
  | { type: "loaded"; key: string; items: Meme[]; nextCursor: string | null; hasNext: boolean }
  | { type: "appended"; key: string; items: Meme[]; nextCursor: string | null; hasNext: boolean }
  | { type: "failed"; key: string }
  | { type: "addability"; items: AddabilityItem[] }

export const initialSearchState: SearchState = {
  key: null, items: [], nextCursor: null, hasNext: false, failed: false, addability: {},
}

export function searchReducer(state: SearchState, action: SearchAction): SearchState {
  switch (action.type) {
    case "loaded":
      return { ...state, key: action.key, items: action.items, nextCursor: action.nextCursor,
        hasNext: action.hasNext, failed: false }
    case "appended": {
      if (state.key !== action.key) return state
      const seen = new Set(state.items.map((item) => item.id))
      const fresh = action.items.filter((item) => !seen.has(item.id))
      return { ...state, items: [...state.items, ...fresh], nextCursor: action.nextCursor,
        hasNext: action.hasNext, failed: false }
    }
    case "failed":
      return { ...state, key: action.key, items: state.key === action.key ? state.items : [],
        hasNext: false, failed: true }
    case "addability": {
      const addability = { ...state.addability }
      for (const item of action.items) addability[item.imageId] = item
      return { ...state, addability }
    }
  }
}

/** Panel ids joined, or null before the comics has loaded; changes whenever panels are added, removed or moved. */
export function panelSignature(panels: { id: string }[] | null): string | null {
  return panels === null ? null : panels.map((panel) => panel.id).join(",")
}

/** The full panel id order after moving the panel at `index` by `delta`, or null when the move is out of range. */
export function movedOrder(panelIds: string[], index: number, delta: -1 | 1): string[] | null {
  const target = index + delta
  if (index < 0 || index >= panelIds.length || target < 0 || target >= panelIds.length) return null
  const next = [...panelIds]
  const moved = next[index]
  next[index] = next[target]
  next[target] = moved
  return next
}
