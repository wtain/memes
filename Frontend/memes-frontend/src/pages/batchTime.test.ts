import { describe, expect, it } from "vitest"
import { formatAbsolute, formatDuration, formatRelative, runDurationMs } from "./batchTime"

const NOW = Date.parse("2026-11-28T12:00:00Z")

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [400, "0s"],
    [45_000, "45s"],
    [125_000, "2m 5s"],
    [3_600_000, "1h"],
    [3 * 3_600_000 + 4 * 60_000 + 30_000, "3h 4m"],
    [26 * 3_600_000, "1d 2h"],
    [48 * 3_600_000, "2d"],
  ])("%i ms -> %s", (ms, text) => {
    expect(formatDuration(ms)).toBe(text)
  })
})

describe("formatRelative", () => {
  it.each([
    ["2026-11-28T11:59:40Z", "just now"],
    ["2026-11-28T11:55:00Z", "5 min ago"],
    ["2026-11-28T09:00:00Z", "3 h ago"],
    ["2026-11-26T12:00:00Z", "2 days ago"],
  ])("%s -> %s", (iso, text) => {
    expect(formatRelative(iso, NOW)).toBe(text)
  })

  it("falls back to the absolute time after a week", () => {
    expect(formatRelative("2026-11-01T10:00:00Z", NOW)).toBe(formatAbsolute("2026-11-01T10:00:00Z"))
  })

  it("never says 'in the future' for a clock a little ahead", () => {
    expect(formatRelative("2026-11-28T12:00:05Z", NOW)).toBe("just now")
  })

  it("is empty for an unparseable value", () => {
    expect(formatRelative("nope", NOW)).toBe("")
  })
})

describe("formatAbsolute", () => {
  it("shows the date and the time in the viewer's zone", () => {
    const text = formatAbsolute("2026-11-28T12:34:00Z")
    expect(text).toContain("2026")
    expect(text).toMatch(/\d{1,2}:\d{2}/)
  })
})

describe("runDurationMs", () => {
  const base = { created_at: "2026-11-28T10:00:00Z" }

  it("is completed minus created for a finished run", () => {
    expect(runDurationMs({ ...base, completed_at: "2026-11-28T10:01:30Z", status: "failed" }, NOW)).toBe(90_000)
  })

  it("is now minus created for a running run", () => {
    expect(runDurationMs({ ...base, completed_at: null, status: "running" }, NOW)).toBe(2 * 3_600_000)
  })

  it("is null for a run that is neither finished nor running", () => {
    expect(runDurationMs({ ...base, completed_at: null, status: "failed" }, NOW)).toBeNull()
  })
})
