import type { BatchProgress } from "../types/generated/all"

export const STALE_AFTER_MS = 300_000

const nf = new Intl.NumberFormat("en-US")

type Counts = { done: number | null; total: number | null }

function isNum(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v)
}

export function formatCount(n: number): string {
  return nf.format(n)
}

export function progressFraction(p: Counts): number | null {
  if (!isNum(p.done) || !isNum(p.total) || p.total <= 0) return null
  return Math.min(1, Math.max(0, p.done / p.total))
}

export function formatEta(p: Counts & { rate_per_min: number | null }): string | null {
  if (!isNum(p.rate_per_min) || p.rate_per_min <= 0) return null
  if (!isNum(p.total) || !isNum(p.done) || p.done >= p.total) return null
  const minutes = (p.total - p.done) / p.rate_per_min
  if (!Number.isFinite(minutes)) return null
  if (minutes < 1) return "under 1 min left"
  if (minutes < 90) return `about ${Math.round(minutes)} min left`
  return `about ${Math.round(minutes / 60)} h left`
}

export function isStalled(updatedAt: string, now: number, status: string): boolean {
  if (status !== "running") return false
  const t = Date.parse(updatedAt)
  return Number.isFinite(t) && now - t > STALE_AFTER_MS
}

function formatAge(updatedAt: string, now: number): string {
  const t = Date.parse(updatedAt)
  if (!Number.isFinite(t)) return ""
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return `updated ${s} s ago`
  const m = Math.floor(s / 60)
  if (m < 60) return `updated ${m} min ago`
  return `updated ${Math.floor(m / 60)} h ago`
}

/** The stalled hint: whole minutes since the last update, never below the threshold's 5. */
function formatStalled(updatedAt: string, now: number): string {
  const m = Math.floor((now - Date.parse(updatedAt)) / 60_000)
  return `no update for ${Math.max(Math.floor(STALE_AFTER_MS / 60_000), m)} min`
}

export function describeProgress(p: BatchProgress, now: number): {
  bar: number | null
  counts: string
  eta: string | null
  step: string | null
  updated: string
  stalled: boolean
} {
  const unit = p.unit ?? "items"
  let counts = ""
  if (isNum(p.done) && isNum(p.total) && p.total > 0) counts = `${formatCount(p.done)} / ${formatCount(p.total)} ${unit}`
  else if (isNum(p.done)) counts = `${formatCount(p.done)} ${unit}`
  const stalled = isStalled(p.updated_at, now, "running")
  return {
    bar: progressFraction(p),
    counts,
    eta: formatEta(p),
    step: p.step ? `step ${p.step.index} of ${p.step.count}: ${p.step.label}` : null,
    updated: stalled ? formatStalled(p.updated_at, now) : formatAge(p.updated_at, now),
    stalled,
  }
}

/** Compact "done / total" for the runs table, "—" when there is nothing to show. */
export function tableFigure(p: BatchProgress | null): string {
  if (!p || !isNum(p.done)) return "—"
  if (isNum(p.total) && p.total > 0) return `${formatCount(p.done)} / ${formatCount(p.total)}`
  return formatCount(p.done)
}
