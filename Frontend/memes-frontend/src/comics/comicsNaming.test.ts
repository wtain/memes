import { defaultComicsName, extractUuid } from "./comicsNaming"

const ID = "0f8fad5b-d9cb-469f-a165-70867728950e"

describe("extractUuid", () => {
  it("finds a bare id, lower-cased", () => {
    expect(extractUuid(ID.toUpperCase())).toBe(ID)
  })
  it("finds an id inside a pasted URL", () => {
    expect(extractUuid(`http://localhost:5173/meme/${ID}?x=1`)).toBe(ID)
  })
  it("returns null when there is none", () => {
    expect(extractUuid("funny cat")).toBeNull()
    expect(extractUuid("0f8fad5b-d9cb-469f-a165")).toBeNull()
  })
})

describe("defaultComicsName", () => {
  it("uses the file name without extension", () => {
    expect(defaultComicsName("cat.story.jpg", ID)).toBe("Comics cat.story")
  })
  it("removes forbidden and control characters", () => {
    expect(defaultComicsName('a<b>:"c/d\\e|f?g*\u0001h.png', ID)).toBe("Comics abcdefgh")
  })
  it("falls back to the id prefix with no usable stem", () => {
    expect(defaultComicsName(null, ID)).toBe("Comics 0f8fad5b")
    expect(defaultComicsName("???.jpg", ID)).toBe("Comics 0f8fad5b")
    expect(defaultComicsName("", ID)).toBe("Comics 0f8fad5b")
  })
  it("keeps Cyrillic", () => {
    expect(defaultComicsName("Кошки.jpg", ID)).toBe("Comics Кошки")
  })
  it("fits 80 characters and never ends with a dot or space", () => {
    const name = defaultComicsName(`${"a".repeat(72)} .${"b".repeat(30)}.jpg`, ID)
    expect(name.length).toBeLessThanOrEqual(80)
    expect(name).not.toMatch(/[. ]$/)
    expect(defaultComicsName(`${"a".repeat(200)}.jpg`, ID)).toHaveLength(80)
  })
  it("appends the id prefix for the retry and still fits", () => {
    expect(defaultComicsName("cat.jpg", ID, true)).toBe("Comics cat 0f8fad5b")
    const long = defaultComicsName(`${"a".repeat(200)}.jpg`, ID, true)
    expect(long).toHaveLength(80)
    expect(long.endsWith(" 0f8fad5b")).toBe(true)
  })
})
