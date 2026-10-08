import type { Filters, MemeFromTextRequest } from "./types"
import { AXES } from "./types"

export function toRequestFilters(filters: Filters): MemeFromTextRequest["filters"] | undefined {
  const out: Record<string, string[]> = {}
  for (const axis of AXES) {
    if (filters[axis].length > 0) out[axis] = filters[axis]
  }
  return Object.keys(out).length > 0 ? out : undefined
}

export function hasFilters(filters: Filters): boolean {
  return AXES.some((axis) => filters[axis].length > 0)
}

export function countFilters(filters: Filters): number {
  return AXES.reduce((sum, axis) => sum + filters[axis].length, 0)
}
