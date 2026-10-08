import type { Saved } from "./storage"
import type { Facet, Filters, MemeFromTextRequest, MemeFromTextResponse, MemeFromTextResult, QueryProfile, ResponseMeta } from "./types"
import { EMPTY_FILTERS } from "./types"
import type { Mode } from "./requests"

export type Status = "idle" | "reading" | "refining" | "ready" | "degraded" | "unavailable" | "error"
export type ErrorKind = "validation" | "failed"

export type State = {
  text: string
  submitted: string | null
  originalProfile: QueryProfile | null
  editedProfile: QueryProfile | null
  filters: Filters
  items: MemeFromTextResult[]
  facets: Facet[]
  nextCursor: string | null
  hasNext: boolean
  fallback: string
  meta: ResponseMeta | null
  /** The request body (without cursor) that produced the current list; "Show more" replays it. */
  lastRequest: MemeFromTextRequest | null
  status: Status
  error: ErrorKind | null
  loadingMore: boolean
}

export type Action =
  | { type: "text"; text: string }
  | { type: "findStarted"; submitted: string }
  | { type: "refineStarted" }
  | { type: "moreStarted" }
  | { type: "succeeded"; mode: Mode; response: MemeFromTextResponse; request: MemeFromTextRequest }
  | { type: "failed"; error: ErrorKind }
  | { type: "edited"; profile: QueryProfile }
  | { type: "resetEdit" }
  | { type: "filters"; filters: Filters }

export function initialState(saved: Saved | null): State {
  return {
    text: saved?.text ?? "",
    submitted: saved?.submitted ?? null,
    originalProfile: saved?.originalProfile ?? null,
    editedProfile: saved?.editedProfile ?? null,
    filters: saved?.filters ?? { ...EMPTY_FILTERS },
    items: [], facets: [], nextCursor: null, hasNext: false, fallback: "none", meta: null, lastRequest: null,
    status: saved?.submitted ? "refining" : "idle",
    error: null, loadingMore: false,
  }
}

export function statusFor(response: MemeFromTextResponse): Status {
  if (!response.meta.available) return "unavailable"
  return response.degraded ? "degraded" : "ready"
}

export function activeProfile(state: State): QueryProfile | null {
  return state.editedProfile ?? state.originalProfile
}

export function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "text":
      return { ...state, text: action.text }
    case "findStarted":
      return {
        ...state, submitted: action.submitted, originalProfile: null, editedProfile: null, filters: { ...EMPTY_FILTERS },
        items: [], facets: [], nextCursor: null, hasNext: false, fallback: "none", meta: null, lastRequest: null,
        status: "reading", error: null, loadingMore: false,
      }
    case "refineStarted":
      return { ...state, status: "refining", error: null }
    case "moreStarted":
      return { ...state, loadingMore: true, error: null }
    case "succeeded": {
      const { response, mode, request } = action
      const { cursor: _cursor, ...requestWithoutCursor } = request
      void _cursor
      const common = {
        facets: response.facets ?? [], nextCursor: response.nextCursor ?? null, hasNext: response.hasNext,
        fallback: response.fallback, meta: response.meta, status: statusFor(response), error: null, loadingMore: false,
        lastRequest: requestWithoutCursor,
      }
      if (mode === "more") {
        // A stale cursor restarts the list server-side: its first item is then the one already shown first.
        const restarted = state.items.length > 0 && response.items.length > 0 && state.items[0].meme.id === response.items[0].meme.id
        return { ...state, ...common, items: restarted ? response.items : [...state.items, ...response.items] }
      }
      return {
        ...state, ...common, items: response.items,
        originalProfile: mode === "find" || state.originalProfile === null ? response.profile : state.originalProfile,
      }
    }
    case "failed":
      return { ...state, status: "error", error: action.error, loadingMore: false }
    case "edited":
      return { ...state, editedProfile: action.profile }
    case "resetEdit":
      return { ...state, editedProfile: null }
    case "filters":
      return { ...state, filters: action.filters }
  }
}
