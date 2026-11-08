/** The comics that clicks on "Add to comics" in result lists go to; remembered in localStorage. */
export type WorkingComics = { id: string; name: string }

export const WORKING_COMICS_KEY = "comics.workingComics"

const listeners = new Set<() => void>()
// Keeps the snapshot referentially stable between reads, as useSyncExternalStore requires.
let cached: { raw: string | null; value: WorkingComics | null } | null = null
// Without usable storage the value lives in memory only, so the bar and cards still agree within a page.
let memory: WorkingComics | null = null
let storageWorks = true

function readRaw(): string | null {
  try {
    return localStorage.getItem(WORKING_COMICS_KEY)
  } catch {
    return null
  }
}

function parse(raw: string | null): WorkingComics | null {
  if (raw === null) return null
  try {
    const v = JSON.parse(raw) as Partial<WorkingComics> | null
    return v && typeof v.id === "string" && typeof v.name === "string" ? { id: v.id, name: v.name } : null
  } catch {
    return null
  }
}

export function getWorkingComics(): WorkingComics | null {
  if (!storageWorks) return memory
  const raw = readRaw()
  if (cached === null || cached.raw !== raw) cached = { raw, value: parse(raw) }
  return cached.value
}

function emit() {
  listeners.forEach(l => l())
}

export function setWorkingComics(value: WorkingComics): void {
  memory = value
  try {
    localStorage.setItem(WORKING_COMICS_KEY, JSON.stringify(value))
    storageWorks = true
  } catch {
    storageWorks = false
  }
  emit()
}

export function clearWorkingComics(): void {
  memory = null
  try {
    localStorage.removeItem(WORKING_COMICS_KEY)
    storageWorks = true
  } catch {
    // Nothing stored, nothing to clear.
  }
  emit()
}

export function subscribeWorkingComics(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}
