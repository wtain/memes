import { countText, per100Text, searchLink, stampText } from "./format"

describe("per100Text", () => {
  it("shows a finite ratio with one decimal", () => {
    expect(per100Text({ per100: 40, per100Infinite: false }, true)).toBe("40.0")
    expect(per100Text({ per100: 40.04, per100Infinite: false }, true)).toBe("40.0")
    expect(per100Text({ per100: 0, per100Infinite: false }, true)).toBe("0.0")
  })

  it("shows the infinity sign for a meme re-dropped and never shown", () => {
    expect(per100Text({ per100: null, per100Infinite: true }, true)).toBe("∞")
  })

  it("shows n/a without impression data, whatever the item says", () => {
    expect(per100Text({ per100: 40, per100Infinite: false }, false)).toBe("n/a")
    expect(per100Text({ per100: null, per100Infinite: true }, false)).toBe("n/a")
    expect(per100Text({ per100: null, per100Infinite: false }, true)).toBe("n/a")
  })
})

describe("countText and stampText", () => {
  it("formats counts", () => {
    expect(countText(null)).toBe("n/a")
    expect(countText(7)).toBe("7")
    expect(countText(0)).toBe("0")
  })

  it("formats timestamps in UTC to the minute", () => {
    expect(stampText("2026-11-01T12:30:00Z")).toBe("2026-11-01 12:30")
    expect(stampText(null)).toBe("-")
  })
})

describe("searchLink", () => {
  it("fills the query", () => {
    expect(searchLink("funny cat", [], "search")).toBe("/search?q=funny+cat")
  })

  it("fills facets for a facet-only search", () => {
    expect(searchLink("", ["animal:cat"], "search")).toBe("/search?animal=cat")
  })

  it("encodes & and # and repeats a category", () => {
    expect(searchLink("a&b #1", ["topic:work", "topic:home"], "search"))
      .toBe("/search?q=a%26b+%231&topic=work&topic=home")
  })

  it("encodes non-Latin text", () => {
    expect(searchLink("смешной кот", [], "search"))
      .toBe("/search?q=" + encodeURIComponent("смешной кот").replace(/%20/g, "+"))
  })

  it("splits a facet on the first colon only", () => {
    expect(searchLink("x", ["topic:a:b"], "search")).toBe("/search?q=x&topic=a%3Ab")
  })

  it("has no link for a meme-from-text row", () => {
    expect(searchLink("x", [], "meme_text")).toBeNull()
  })
})
