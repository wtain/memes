import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"

export const EXAMPLES_KEY = "home.examples.v1"

const MAX_EXAMPLES = 3
const MIN_EXAMPLES = 2
const MAX_EXAMPLE_CHARS = 24
/** One token: the search ANDs the lemmas of every token, so a single word is the safe example. */
const SINGLE_TOKEN = /^[\p{L}\p{N}]{3,24}$/u

/** Single-word tag names of the given memes, most frequent first (ties by name). */
export function pickExampleCandidates(memes: Meme[], max = 6): string[] {
  const counts = new Map<string, number>()
  for (const meme of memes) {
    for (const tag of meme.tags ?? []) {
      if (!SINGLE_TOKEN.test(tag.name)) continue
      const name = tag.name.toLowerCase()
      counts.set(name, (counts.get(name) ?? 0) + 1)
    }
  }
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
    .slice(0, max)
    .map(([name]) => name)
}

/** The verified examples cached for this tab, or null when absent or not trustworthy. */
export function readCachedExamples(): string[] | null {
  try {
    const raw = sessionStorage.getItem(EXAMPLES_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length > MAX_EXAMPLES) return null
    if (!parsed.every(v => typeof v === "string" && v.length > 0 && v.length <= MAX_EXAMPLE_CHARS)) return null
    return parsed as string[]
  } catch {
    return null
  }
}

/** Keeps the candidates (at most three, in order) whose search returns three memes; caches two or more. */
export async function verifyExamples(
  memesApi: MemesApi,
  candidates: string[],
  signal?: AbortSignal,
): Promise<string[]> {
  if (signal?.aborted) return []
  const probes = await Promise.all(
    candidates.map(query =>
      memesApi.searchMemes({ query, limit: 3, record: false }).then(
        response => (response.items?.length ?? 0) >= 3,
        () => false,
      ),
    ),
  )
  if (signal?.aborted) return []
  const verified = candidates.filter((_, i) => probes[i]).slice(0, MAX_EXAMPLES)
  if (verified.length < MIN_EXAMPLES) return []
  try {
    sessionStorage.setItem(EXAMPLES_KEY, JSON.stringify(verified))
  } catch {
    // storage blocked: the examples just are not cached
  }
  return verified
}
