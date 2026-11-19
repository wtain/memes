import { describe, expect, it } from "vitest"
import { buildFileName, extensionForMime } from "./filename"

describe("extensionForMime", () => {
  it("maps known types and defaults to jpg", () => {
    expect(extensionForMime("image/jpeg")).toBe("jpg")
    expect(extensionForMime("image/png")).toBe("png")
    expect(extensionForMime("image/gif")).toBe("gif")
    expect(extensionForMime("image/webp")).toBe("webp")
    expect(extensionForMime("application/x")).toBe("jpg")
  })
})

describe("buildFileName", () => {
  const id = "abcdef123456"
  it("keeps a clean original name", () => {
    expect(buildFileName({ id, originalFileName: "cat.png" }, "image/png")).toBe("cat.png")
  })
  it("keeps only the last segment and replaces illegal characters", () => {
    const name = buildFileName({ id, originalFileName: "C:\\x\\..\\a:b*?.png" }, "image/png")
    expect(name).toBe("a_b__.png")
    expect(name).not.toMatch(/[\\/:*?"<>|]/)
  })
  it("replaces control characters", () => {
    expect(buildFileName({ id, originalFileName: "a\u0001b.png" }, "image/png")).toBe("a_b.png")
  })
  it("adds an extension from the mime type when missing", () => {
    expect(buildFileName({ id, originalFileName: "cat" }, "image/gif")).toBe("cat.gif")
  })
  it("caps at 120 chars keeping the extension", () => {
    const name = buildFileName({ id, originalFileName: "a".repeat(300) + ".png" }, "image/png")
    expect(name.length).toBe(120)
    expect(name.endsWith(".png")).toBe(true)
  })
  it("falls back for missing or blank names", () => {
    for (const originalFileName of [undefined, "", "   "]) {
      expect(buildFileName({ id, originalFileName }, "image/jpeg")).toBe("meme-abcdef12.jpg")
    }
  })
})
