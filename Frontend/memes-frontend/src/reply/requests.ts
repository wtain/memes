import { toRequestFilters } from "./filters"
import type { State } from "./messageSearchReducer"
import type { Filters, MemeFromTextRequest, QueryProfile } from "./types"
import { PAGE_SIZE } from "./types"

export type Mode = "find" | "refine" | "more"
export type Overrides = { editedProfile?: QueryProfile | null; filters?: Filters }

/** The profile worth sending: an edited one, or a usable (non-degraded) original. A degraded original is empty and would rank nothing. */
export function profileToSend(state: State, overrides: Overrides = {}): QueryProfile | null {
  const edited = overrides.editedProfile !== undefined ? overrides.editedProfile : state.editedProfile
  if (edited) return edited
  const original = state.originalProfile
  return original && !original.degraded ? original : null
}

export function buildRequest(state: State, mode: Mode, overrides: Overrides = {}): MemeFromTextRequest {
  if (mode === "find") return { text: state.text.trim(), limit: PAGE_SIZE }

  // "Show more" replays the exact request that produced the list: the server ties a cursor to the request.
  if (mode === "more" && state.lastRequest && state.nextCursor) {
    return { ...state.lastRequest, cursor: state.nextCursor }
  }

  const request: MemeFromTextRequest = { text: state.submitted ?? state.text.trim(), limit: PAGE_SIZE }
  const profile = profileToSend(state, overrides)
  if (profile) request.profile = profile
  const filters = toRequestFilters(overrides.filters ?? state.filters)
  if (filters) request.filters = filters
  return request
}
