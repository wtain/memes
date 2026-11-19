import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  MAX_QUERY_CHARS,
  MAX_RECENT,
  RECENT_SEARCHES_KEY,
  clearRecentSearches,
  readRecentSearches,
  rememberSearch,
} from "./recentSearches"

describe("recentSearches", () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("keeps the newest search first", () => {
    rememberSearch("cats")
    rememberSearch("dogs")
    expect(readRecentSearches()).toEqual(["dogs", "cats"])
  })

  it("dedupes case-insensitively and keeps the new casing", () => {
    rememberSearch("cats")
    rememberSearch("dogs")
    rememberSearch("Cats")
    expect(readRecentSearches()).toEqual(["Cats", "dogs"])
  })

  it("keeps only the newest five", () => {
    for (const q of ["a1", "b2", "c3", "d4", "e5", "f6"]) rememberSearch(q)
    expect(MAX_RECENT).toBe(5)
    expect(readRecentSearches()).toEqual(["f6", "e5", "d4", "c3", "b2"])
  })

  it("trims, ignores blank text and caps the length", () => {
    rememberSearch("  pizza  ")
    rememberSearch("   ")
    rememberSearch("")
    expect(readRecentSearches()).toEqual(["pizza"])
    rememberSearch("x".repeat(MAX_QUERY_CHARS + 50))
    expect(readRecentSearches()[0]).toBe("x".repeat(MAX_QUERY_CHARS))
  })

  it("keeps a long but ordinary query whole so the chip reruns the same search", () => {
    const long = "word ".repeat(30).trim()
    expect(long.length).toBeGreaterThan(100)
    rememberSearch(long)
    expect(readRecentSearches()).toEqual([long])
  })

  it("clears everything", () => {
    rememberSearch("cats")
    clearRecentSearches()
    expect(readRecentSearches()).toEqual([])
  })

  it.each([
    ["not json"],
    ["{}"],
    ["null"],
    ['"str"'],
  ])("returns [] for stored garbage %s", raw => {
    localStorage.setItem(RECENT_SEARCHES_KEY, raw)
    expect(readRecentSearches()).toEqual([])
  })

  it("drops non-strings from a mixed array", () => {
    localStorage.setItem(RECENT_SEARCHES_KEY, '[1,"ok",null,"  two  "]')
    expect(readRecentSearches()).toEqual(["ok", "two"])
  })

  it("bounds a huge stored list", () => {
    const many = Array.from({ length: 1000 }, (_, i) => "q".repeat(200) + i)
    localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(many))
    const out = readRecentSearches()
    expect(out.length).toBeLessThanOrEqual(5)
    out.forEach(q => expect(q.length).toBeLessThanOrEqual(MAX_QUERY_CHARS))
  })

  it("survives storage that throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    expect(readRecentSearches()).toEqual([])
    expect(() => rememberSearch("x")).not.toThrow()
    expect(() => clearRecentSearches()).not.toThrow()
  })
})
