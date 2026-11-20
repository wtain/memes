import { readFileSync } from "node:fs"
import { describe, it, expect, vi } from "vitest"
import { PAGE_ALLOWLIST, sendPageview, templateFor } from "./pageview"

describe("templateFor", () => {
  it("returns the route template, never the concrete URL", () => {
    expect(templateFor("/memes/abc-123")).toBe("/memes/:id")
    expect(templateFor("/concepts/42")).toBe("/concepts/:id")
    expect(templateFor("/trends/date/2026-01-02")).toBe("/trends/date/:date")
    expect(templateFor("/trends/history/PER/Putin")).toBe("/trends/history/:label/:name")
    expect(templateFor("/")).toBe("/")
    expect(templateFor("/explore")).toBe("/explore")
  })
  it("returns null for admin and unmatched routes", () => {
    expect(templateFor("/admin")).toBeNull()
    expect(templateFor("/admin/comics/7")).toBeNull()
    expect(templateFor("/nope")).toBeNull()
  })
  it("covers every non-admin route of router.tsx and nothing else", () => {
    const src = readFileSync("src/app/router.tsx", "utf8")
    const paths = [...src.matchAll(/path:\s*"([^"]+)"/g)].map((m) => m[1])
    const nonAdmin = paths.filter((p) => !p.startsWith("/admin"))
    expect(nonAdmin.length).toBeGreaterThan(10)
    for (const p of nonAdmin) {
      expect(PAGE_ALLOWLIST).toContain(p)
    }
    expect([...PAGE_ALLOWLIST].sort()).toEqual([...nonAdmin].sort())
    for (const p of paths.filter((p) => p.startsWith("/admin"))) {
      expect(templateFor(p.replace(/:\w+/g, "1"))).toBeNull()
    }
  })
})

describe("sendPageview", () => {
  it("sends the template via sendBeacon in prod", () => {
    const sendBeacon = vi.fn(() => true)
    const fetch = vi.fn()
    sendPageview("/memes/abc-123", { fetch, sendBeacon, stage: "prod", baseUrl: "http://x" })
    expect(fetch).not.toHaveBeenCalled()
    expect(sendBeacon).toHaveBeenCalledTimes(1)
    const calls = sendBeacon.mock.calls as unknown as [string, Blob][]
    expect(calls[0][0]).toBe("http://x/api/telemetry/pageview")
    return calls[0][1].text().then((t) => {
      expect(JSON.parse(t)).toEqual({ client: "web", page: "/memes/:id" })
      expect(t).not.toContain("abc-123")
    })
  })
  it("does nothing when the stage is not prod", () => {
    const sendBeacon = vi.fn()
    const fetch = vi.fn()
    sendPageview("/explore", { fetch, sendBeacon, stage: undefined, baseUrl: "" })
    sendPageview("/explore", { fetch, sendBeacon, stage: "dev", baseUrl: "" })
    expect(sendBeacon).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })
  it("sends nothing for admin or unknown paths", () => {
    const sendBeacon = vi.fn()
    sendPageview("/admin/recut", { fetch: vi.fn(), sendBeacon, stage: "prod", baseUrl: "" })
    expect(sendBeacon).not.toHaveBeenCalled()
  })
  it("falls back to fetch with keepalive when sendBeacon is absent", () => {
    const fetch = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })))
    sendPageview("/search", { fetch, sendBeacon: undefined, stage: "prod", baseUrl: "http://x" })
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("http://x/api/telemetry/pageview")
    expect(init.keepalive).toBe(true)
    expect(init.method).toBe("POST")
    expect(JSON.parse(init.body as string)).toEqual({ client: "web", page: "/search" })
  })
  it("swallows a rejected fetch and a throwing sendBeacon", async () => {
    const fetch = vi.fn(() => Promise.reject(new Error("down")))
    expect(() => sendPageview("/", { fetch, sendBeacon: undefined, stage: "prod", baseUrl: "" })).not.toThrow()
    await Promise.resolve()
    const sendBeacon = vi.fn(() => {
      throw new Error("boom")
    })
    expect(() => sendPageview("/", { fetch, sendBeacon, stage: "prod", baseUrl: "" })).not.toThrow()
  })
})
