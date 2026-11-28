export type RunTimes = { created_at: string; completed_at: string | null; status: string }

const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** "45s", "2m 5s", "3h 4m", "1d 2h": the two largest units, a zero second part dropped. */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / SECOND))
  const d = Math.floor(total / 86_400)
  const h = Math.floor((total % 86_400) / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const parts: [number, string][] =
    d > 0 ? [[d, "d"], [h, "h"]] : h > 0 ? [[h, "h"], [m, "m"]] : m > 0 ? [[m, "m"], [s, "s"]] : [[s, "s"]]
  return parts.filter(([n], i) => i === 0 || n > 0).map(([n, unit]) => `${n}${unit}`).join(" ")
}

/** The viewer's local date and time, e.g. "28 Nov 2026, 14:24". */
export function formatAbsolute(iso: string): string {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ""
  return new Date(t).toLocaleString("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
  })
}

/** "just now", "5 min ago", "3 h ago", "2 days ago"; the absolute time once it is a week old. */
export function formatRelative(iso: string, now: number): string {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ""
  const age = now - t
  if (age < MINUTE) return "just now"
  if (age < HOUR) return `${Math.floor(age / MINUTE)} min ago`
  if (age < DAY) return `${Math.floor(age / HOUR)} h ago`
  if (age < 7 * DAY) return `${Math.floor(age / DAY)} days ago`
  return formatAbsolute(iso)
}

/** Time a run took (finished) or has taken so far (running); null when it has no end and is not running. */
export function runDurationMs(run: RunTimes, now: number): number | null {
  const start = Date.parse(run.created_at)
  if (!Number.isFinite(start)) return null
  if (run.completed_at) {
    const end = Date.parse(run.completed_at)
    return Number.isFinite(end) ? Math.max(0, end - start) : null
  }
  return run.status === "running" ? Math.max(0, now - start) : null
}
