import { truncateMiddle } from "./truncateMiddle"

const LONG = "IMG_20260101_123456_some_extremely_long_descriptive_name_final_version.jpg"

describe("truncateMiddle", () => {
  it("returns short text unchanged", () => {
    expect(truncateMiddle("a.jpg")).toBe("a.jpg")
    expect(truncateMiddle("")).toBe("")
  })

  it("returns text of exactly max length unchanged", () => {
    const exact = "x".repeat(28) + ".jpg"
    expect(exact).toHaveLength(32)
    expect(truncateMiddle(exact)).toBe(exact)
  })

  it("cuts the middle to exactly max characters, keeping the start and the extension", () => {
    const out = truncateMiddle(LONG)
    expect(out).toHaveLength(32)
    expect(out).toContain("…")
    expect(out.startsWith("IMG_2026")).toBe(true)
    expect(out.endsWith(".jpg")).toBe(true)
    expect(out.endsWith(LONG.slice(-12))).toBe(true) // the end of the name survives, not just the extension
  })

  it("honours a custom max", () => {
    expect(truncateMiddle(LONG, 20)).toHaveLength(20)
    expect(truncateMiddle(LONG, 20).endsWith(".jpg")).toBe(true)
  })

  it("cuts a name without an extension too", () => {
    const out = truncateMiddle("a".repeat(100))
    expect(out).toHaveLength(32)
    expect(out).toContain("…")
  })

  it("does not treat a very long suffix after the last dot as an extension", () => {
    const out = truncateMiddle("a".repeat(100) + "." + "b".repeat(50))
    expect(out).toHaveLength(32)
    expect(out).toContain("…")
  })

  it("does not treat a leading dot as an extension", () => {
    const out = truncateMiddle("." + "h".repeat(60))
    expect(out).toHaveLength(32)
    expect(out.startsWith(".h")).toBe(true)
  })

  it("falls back to cutting the end for a tiny max, never exceeding it", () => {
    expect(truncateMiddle("abcdefghij", 4)).toHaveLength(4)
    expect(truncateMiddle("abcdefghij", 4).endsWith("…")).toBe(true)
    expect(truncateMiddle("abcdefghij", 1)).toBe("…")
    expect(truncateMiddle("abcdefghij", 0)).toBe("")
  })

  it("counts and cuts by code point, never splitting an emoji", () => {
    const out = truncateMiddle("😀".repeat(50))
    expect(Array.from(out)).toHaveLength(32)
    expect(out).toMatch(/^(?:[\uD800-\uDBFF][\uDC00-\uDFFF]|[^\uD800-\uDFFF])*$/)
  })
})
