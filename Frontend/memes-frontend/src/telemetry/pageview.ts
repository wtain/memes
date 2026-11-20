import { matchPath } from "react-router-dom"
import type { PageViewBeacon } from "../types/generated/all"

/**
 * Route templates the server counts by name (any other value is counted as `other` there).
 * Admin routes are deliberately absent: they are never sent. A test keeps this list equal to the
 * non-admin routes of app/router.tsx.
 */
export const PAGE_ALLOWLIST: readonly string[] = [
  "/",
  "/explore",
  "/untagged",
  "/duplicates",
  "/no-ocr",
  "/flagged",
  "/trending-memes",
  "/trends",
  "/trends/date/:date",
  "/trends/history/:label/:name",
  "/reply",
  "/search",
  "/recommendations",
  "/concepts",
  "/upload",
  "/statistics",
  "/ingestion",
  "/memes/:id",
  "/concepts/:id",
]

/** The route template a concrete pathname matches, or null (admin and unmatched routes are not reported). */
export function templateFor(pathname: string): string | null {
  for (const template of PAGE_ALLOWLIST) {
    if (matchPath({ path: template, end: true }, pathname) !== null) return template
  }
  return null
}

export interface PageviewDeps {
  fetch?: (url: string, init: RequestInit) => Promise<unknown>
  sendBeacon?: (url: string, data: Blob) => boolean
  stage?: string
  baseUrl?: string
}

function defaultDeps(): PageviewDeps {
  return {
    fetch: typeof fetch === "function" ? (u, i) => fetch(u, i) : undefined,
    sendBeacon:
      typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function"
        ? (u, d) => navigator.sendBeacon(u, d)
        : undefined,
    stage: import.meta.env.VITE_STAGE,
    baseUrl: import.meta.env.VITE_BACKEND_API_URL,
  }
}

/**
 * Report one page visit: prod builds only, route template only, fire and forget.
 * Accepts a pathname (the template is derived) or an already-known template.
 */
export function sendPageview(pathOrTemplate: string, deps: PageviewDeps = defaultDeps()): void {
  if (deps.stage !== "prod") return
  const page = PAGE_ALLOWLIST.includes(pathOrTemplate) ? pathOrTemplate : templateFor(pathOrTemplate)
  if (page === null) return
  const body: PageViewBeacon = { client: "web", page }
  const url = `${deps.baseUrl ?? ""}/api/telemetry/pageview`
  const json = JSON.stringify(body)
  try {
    if (deps.sendBeacon) {
      if (deps.sendBeacon(url, new Blob([json], { type: "application/json" }))) return
    }
    if (deps.fetch) {
      deps
        .fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: json, keepalive: true })
        ?.catch?.(() => undefined)
    }
  } catch {
    // telemetry must never affect the page
  }
}
