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
  /** True once a find/refine response produced a list on screen (even an empty one); false for a new find and for a restored session until its first response. */
  hasList: boolean
  /** Which reading produced the list on screen (taken from the request that produced it, not from the current edit state). */
  listReading: "parsed" | "edited"
  /** Bumped on every successful response; the page uses it to retry a vocabulary load that failed. */
  searchCount: number
  /** Id of the search_history row of the fresh search that produced the list (epic meme-popularity, S6). Refine and
   *  show-more responses carry none, so this keeps the fresh search's id; never persisted. */
  searchId: string | null
}

export type Action =
  | { type: "text"; text: string }
  | { type: "findStarted"; submitted: string }
  | { type: "refineStarted" }
  | { type: "moreStarted" }
  | { type: "succeeded"; mode: Mode; response: MemeFromTextResponse; request: MemeFromTextRequest; reading?: "parsed" | "edited" }
  | { type: "failed"; error: ErrorKind }
  | { type: "unavailable" }
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
    error: null, loadingMore: false, hasList: false, listReading: "parsed", searchCount: 0, searchId: null,
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
        status: "reading", error: null, loadingMore: false, hasList: false, listReading: "parsed", searchId: null,
      }
    case "refineStarted":
      return { ...state, status: "refining", error: null }
    case "moreStarted":
      return { ...state, loadingMore: true, error: null }
    case "succeeded": {
      const { response, mode, request } = action
      const listReading = mode === "more" ? state.listReading : mode === "find" ? "parsed" : (action.reading ?? "parsed")
      const { cursor: _cursor, ...requestWithoutCursor } = request
      void _cursor
      const common = {
        facets: response.facets ?? [], nextCursor: response.nextCursor ?? null, hasNext: response.hasNext,
        fallback: response.fallback, meta: response.meta, status: statusFor(response), error: null, loadingMore: false,
        lastRequest: requestWithoutCursor, hasList: true, listReading, searchCount: state.searchCount + 1,
        searchId: mode === "find" ? (response.searchId ?? null) : (response.searchId ?? state.searchId),
      }
      if (mode === "more") {
        // When the cached list expires the server recomputes it, and the positional cursor then pages a different
        // ranking. Either its first item is the one already shown first (a restart), or the degraded state flipped.
        const listShown = state.status === "ready" || state.status === "degraded"
        const restarted = state.items.length > 0 && response.items.length > 0 && state.items[0].meme.id === response.items[0].meme.id
        const reranked = listShown && statusFor(response) !== state.status
        if (restarted || reranked) return { ...state, ...common, items: response.items }
        // A normal page: append without repeats; the list's status, fallback, meta and facets stay as they are.
        const shown = new Set(state.items.map((r) => r.meme.id))
        return {
          ...state, nextCursor: common.nextCursor, hasNext: common.hasNext, error: null, loadingMore: false,
          lastRequest: requestWithoutCursor, hasList: true, searchCount: common.searchCount, searchId: common.searchId,
          items: [...state.items, ...response.items.filter((r) => !shown.has(r.meme.id))],
        }
      }
      return {
        ...state, ...common, items: response.items,
        originalProfile: mode === "find" || state.originalProfile === null ? response.profile : state.originalProfile,
      }
    }
    case "failed":
      return { ...state, status: "error", error: action.error, loadingMore: false }
    case "unavailable":
      return { ...state, status: "unavailable", error: null, loadingMore: false }
    case "edited":
      return { ...state, editedProfile: action.profile }
    case "resetEdit":
      return { ...state, editedProfile: null }
    case "filters":
      return { ...state, filters: action.filters }
  }
}
