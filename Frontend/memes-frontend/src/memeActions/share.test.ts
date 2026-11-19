import { afterEach, describe, expect, it, vi } from "vitest"
import { canShareFiles, shareImage } from "./share"

function setNav(props: Record<string, unknown>) {
  for (const [k, v] of Object.entries(props)) {
    Object.defineProperty(navigator, k, { value: v, configurable: true, writable: true })
  }
}
afterEach(() => {
  setNav({ share: undefined, canShare: undefined })
})

describe("canShareFiles", () => {
  it("is false without navigator.share", () => {
    setNav({ share: undefined, canShare: () => true })
    expect(canShareFiles()).toBe(false)
  })
  it("is true with share and canShare", () => {
    setNav({ share: vi.fn(), canShare: () => true })
    expect(canShareFiles()).toBe(true)
  })
})

describe("shareImage", () => {
  it("shares a File with the right name and type", async () => {
    const share = vi.fn().mockResolvedValue(undefined)
    setNav({ share, canShare: () => true })
    await expect(shareImage(new Blob(["x"], { type: "image/png" }), "cat.png")).resolves.toEqual({
      kind: "shared",
    })
    const file = share.mock.calls[0][0].files[0] as File
    expect(file.name).toBe("cat.png")
    expect(file.type).toBe("image/png")
  })
  it("maps AbortError to cancelled", async () => {
    setNav({
      share: vi.fn().mockRejectedValue(new DOMException("x", "AbortError")),
      canShare: () => true,
    })
    await expect(shareImage(new Blob(["x"]), "a.png")).resolves.toEqual({ kind: "cancelled" })
  })
  it("returns error when canShare says no", async () => {
    const share = vi.fn()
    setNav({ share, canShare: () => false })
    await expect(shareImage(new Blob(["x"]), "a.png")).resolves.toEqual({ kind: "error" })
    expect(share).not.toHaveBeenCalled()
  })
  it("returns error on other rejections", async () => {
    setNav({ share: vi.fn().mockRejectedValue(new Error("x")), canShare: () => true })
    await expect(shareImage(new Blob(["x"]), "a.png")).resolves.toEqual({ kind: "error" })
  })
})
