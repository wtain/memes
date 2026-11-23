const ELLIPSIS = "…"
const MAX_EXTENSION = 8 // longest suffix (dot included) still treated as a file extension
const MIN_HEAD_BEFORE_TAIL = 3 // characters kept before the extension inside the tail

/**
 * Shortens `text` to at most `max` characters by cutting out the middle and leaving an ellipsis,
 * so the start of the name and its end (the extension and what precedes it) both stay readable.
 * Counts and cuts by code point, so an emoji is never split.
 */
export function truncateMiddle(text: string, max = 32): string {
  const chars = Array.from(text)
  if (chars.length <= max) return text
  if (max <= 0) return ""
  if (max < 8) return chars.slice(0, max - 1).join("") + ELLIPSIS

  const budget = max - 1 // room for the characters around the ellipsis
  const dot = chars.lastIndexOf(".")
  const extension = dot > 0 && chars.length - dot <= MAX_EXTENSION ? chars.length - dot : 0
  const tail = Math.min(budget - MIN_HEAD_BEFORE_TAIL, Math.max(Math.ceil(budget * 0.4), extension + MIN_HEAD_BEFORE_TAIL))
  const head = budget - tail
  return chars.slice(0, head).join("") + ELLIPSIS + chars.slice(chars.length - tail).join("")
}
