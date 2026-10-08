import type { Filters, QueryProfile } from "./types"
import { AXES, EMPTY_FILTERS } from "./types"

const KEY = "reply.v1"

export type Saved = {
  text: string
  submitted: string | null
  originalProfile: QueryProfile | null
  editedProfile: QueryProfile | null
  filters: Filters
}

export function browserStorage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function cleanFilters(raw: unknown): Filters {
  const filters: Filters = { emotion: [], stance: [], topic: [], tone: [] }
  for (const axis of AXES) {
    const list = (raw as Record<string, unknown> | null)?.[axis]
    if (Array.isArray(list)) filters[axis] = list.filter((item): item is string => typeof item === "string")
  }
  return filters
}

function cleanProfile(raw: unknown): QueryProfile | null {
  if (typeof raw !== "object" || raw === null) return null
  const p = raw as Record<string, unknown>
  const ok = AXES.every((axis) => Array.isArray(p[axis])) && typeof p.degraded === "boolean" && typeof p.taxonomyVersion === "number"
  return ok ? (raw as QueryProfile) : null
}

export function loadSaved(storage: Storage | null): Saved | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(KEY)
    if (!raw) return null
    const data = JSON.parse(raw)
    if (typeof data?.text !== "string") return null
    return {
      text: data.text,
      submitted: typeof data.submitted === "string" ? data.submitted : null,
      originalProfile: cleanProfile(data.originalProfile),
      editedProfile: cleanProfile(data.editedProfile),
      filters: data.filters ? cleanFilters(data.filters) : { ...EMPTY_FILTERS },
    }
  } catch {
    return null
  }
}

export function save(storage: Storage | null, saved: Saved): void {
  if (!storage) return
  try {
    storage.setItem(KEY, JSON.stringify(saved))
  } catch {
    // quota exceeded or storage blocked: the page works without persistence
  }
}
