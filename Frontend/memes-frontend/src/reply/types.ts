import type {
  Facet, MemeFromTextRequest, MemeFromTextResponse, MemeFromTextResult, QueryProfile, TaxonomyResponse,
  RatingItem, RatingRequest, RatingResponse, RatingsResponse,
} from "../types/generated/all"

export type { Facet, MemeFromTextRequest, MemeFromTextResponse, MemeFromTextResult, QueryProfile, TaxonomyResponse, RatingItem, RatingRequest, RatingResponse, RatingsResponse }

export type Grade = 0 | 1 | 2

/** Buttons of the rating bar, best first; the grade is the gold-set grade it maps to. */
export const GRADE_CHOICES = [
  { grade: 2 as const, label: "Good fit" },
  { grade: 1 as const, label: "Acceptable" },
  { grade: 0 as const, label: "Not a fit" },
] as const

/** Everything a rating records about the search besides which meme and which grade. */
export type RatingContext = Omit<RatingRequest, "imageId" | "grade">

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
