import type {
  Facet, MemeFromTextRequest, MemeFromTextResponse, MemeFromTextResult, QueryProfile, TaxonomyResponse,
} from "../types/generated/all"

export type { Facet, MemeFromTextRequest, MemeFromTextResponse, MemeFromTextResult, QueryProfile, TaxonomyResponse }

export const AXES = ["emotion", "stance", "topic", "tone"] as const
export type AxisName = (typeof AXES)[number]

export const AXIS_LABELS: Record<AxisName, string> = {
  emotion: "Emotion", stance: "Stance", topic: "Topic", tone: "Tone",
}

export type Filters = Record<AxisName, string[]>
export const EMPTY_FILTERS: Filters = { emotion: [], stance: [], topic: [], tone: [] }

export const MESSAGE_LIMIT = 600
export const SITUATION_LIMIT = 160
export const PAGE_SIZE = 20
export const DEBOUNCE_MS = 400
// Used only while the vocabulary has not loaded; the taxonomy's maxValues wins when present.
export const DEFAULT_MAX_VALUES: Record<AxisName, number> = { emotion: 3, stance: 2, topic: 3, tone: 2 }

export type ResponseMeta = MemeFromTextResponse["meta"]
