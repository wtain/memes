export const RECENT_SEARCHES_KEY = "home.recentSearches.v1"
export const MAX_RECENT = 5
export const MAX_QUERY_CHARS = 200
/** Chips show at most this many characters; the stored text and the link keep the whole query. */
export const DISPLAY_QUERY_CHARS = 32

export function shortenForChip(query: string): string {
  const chars = [...query]
  return chars.length <= DISPLAY_QUERY_CHARS ? query : `${chars.slice(0, DISPLAY_QUERY_CHARS - 1).join("").trimEnd()}…`
}

function clean(value: unknown): string | null {
  if (typeof value !== "string") return null
  const text = value.trim().slice(0, MAX_QUERY_CHARS).trim()
  return text === "" ? null : text
}

function dedupe(values: unknown[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const value of values) {
    const text = clean(value)
    if (text === null) continue
    const key = text.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(text)
    if (out.length === MAX_RECENT) break
  }
  return out
}

/** The remembered searches, newest first. Storage is untrusted: anything unusable gives []. */
export function readRecentSearches(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_SEARCHES_KEY)
    if (raw === null) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? dedupe(parsed) : []
  } catch {
    return []
  }
}

/** Remember one committed search (per browser, never sent anywhere). Blank text is ignored. */
export function rememberSearch(query: string): void {
  const text = clean(query)
  if (text === null) return
  try {
    const next = dedupe([text, ...readRecentSearches()])
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(next))
  } catch {
    // storage blocked or full: Home just has no row
  }
}

export function clearRecentSearches(): void {
  try {
    localStorage.removeItem(RECENT_SEARCHES_KEY)
  } catch {
    // ignore
  }
}
