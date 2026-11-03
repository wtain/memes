import { sourceLabel } from "./popularitySources"

describe("sourceLabel", () => {
  it("maps a known source to its label", () => {
    expect(sourceLabel("hash_in_batch")).toBe("Identical files dropped together")
  })

  it("labels the manual adjustment source", () => {
    expect(sourceLabel("adjustment")).toBe("Manual adjustment")
  })

  it("labels the comics build source", () => {
    expect(sourceLabel("comics_build")).toBe("Comics built")
  })

  it("falls back to the raw key for an unknown source", () => {
    expect(sourceLabel("manual")).toBe("manual")
  })
})
