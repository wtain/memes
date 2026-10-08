import { labelFor, maxValuesFor, optionsFor } from "./taxonomy"
import { TEST_TAXONOMY } from "./fixtures"

describe("taxonomy lookups", () => {
  it("returns English labels and falls back to the key", () => {
    expect(labelFor(TEST_TAXONOMY, "emotion", "joy")).toBe("Joy")
    expect(labelFor(TEST_TAXONOMY, "emotion", "unknown")).toBe("unknown")
    expect(labelFor(null, "emotion", "joy")).toBe("joy")
  })

  it("lists picker options with definitions", () => {
    const options = optionsFor(TEST_TAXONOMY, "stance")
    expect(options.map((o) => o.key)).toEqual(["sarcasm", "congratulate"])
    expect(options[0].definition).toContain("definition")
    expect(optionsFor(null, "stance")).toEqual([])
  })

  it("uses the taxonomy's max values, or the defaults before it loads", () => {
    expect(maxValuesFor(TEST_TAXONOMY, "emotion")).toBe(3)
    expect(maxValuesFor(null, "stance")).toBe(2)
  })
})
