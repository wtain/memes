import { buildSearchParams } from "../utils/searchParams"

export function per100Text(item: { per100: number | null; per100Infinite: boolean }, available: boolean): string {
  if (!available) return "n/a"
  if (item.per100Infinite) return "∞"
  return item.per100 === null ? "n/a" : item.per100.toFixed(1)
}

export function countText(value: number | null): string {
  return value === null ? "n/a" : String(value)
}

export function stampText(value: string | null): string {
  return value === null ? "-" : value.slice(0, 16).replace("T", " ")
}

export function searchLink(query: string, tags: string[], kind: string): string | null {
  if (kind === "meme_text") return null
  const filters: Record<string, string[]> = {}
  for (const tag of tags) {
    const at = tag.indexOf(":")
    if (at < 0) continue
    const category = tag.slice(0, at)
    filters[category] = [...(filters[category] ?? []), tag.slice(at + 1)]
  }
  return `/search?${buildSearchParams(query, filters).toString()}`
}
