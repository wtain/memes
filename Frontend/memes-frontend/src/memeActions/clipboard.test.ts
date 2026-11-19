import { afterEach, describe, expect, it, vi } from "vitest"
import { canCopyImage, copyText, writeImageToClipboard } from "./clipboard"

function setSecure(value: boolean) {
  Object.defineProperty(window, "isSecureContext", { value, configurable: true })
}
function setClipboard(value: unknown) {
  Object.defineProperty(navigator, "clipboard", { value, configurable: true })
}

class FakeClipboardItem {
  data: Record<string, Promise<Blob> | Blob>
  constructor(data: Record<string, Promise<Blob> | Blob>) {
    this.data = data
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  setSecure(true)
  setClipboard(undefined)
  // jsdom has no execCommand; remove any stub
  delete (document as unknown as Record<string, unknown>).execCommand
})

describe("canCopyImage", () => {
  it("is true when secure, ClipboardItem and clipboard.write exist", () => {
    setSecure(true)
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    setClipboard({ write: vi.fn() })
    expect(canCopyImage()).toBe(true)
  })
  it("is false when not secure", () => {
    setSecure(false)
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    setClipboard({ write: vi.fn() })
    expect(canCopyImage()).toBe(false)
  })
  it("is false without ClipboardItem", () => {
    setSecure(true)
    vi.stubGlobal("ClipboardItem", undefined)
    setClipboard({ write: vi.fn() })
    expect(canCopyImage()).toBe(false)
  })
  it("is false without navigator.clipboard", () => {
    setSecure(true)
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    setClipboard(undefined)
    expect(canCopyImage()).toBe(false)
  })
})

describe("writeImageToClipboard", () => {
  it("calls write synchronously with a promise-valued item", async () => {
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    const write = vi.fn().mockResolvedValue(undefined)
    setClipboard({ write })
    let resolve!: (b: Blob) => void
    const pending = new Promise<Blob>((r) => (resolve = r))
    const result = writeImageToClipboard(pending)
    expect(write).toHaveBeenCalledTimes(1)
    const item = write.mock.calls[0][0][0] as FakeClipboardItem
    expect(item.data["image/png"]).toBe(pending)
    resolve(new Blob(["x"], { type: "image/png" }))
    await expect(result).resolves.toEqual({ kind: "copied" })
  })
  it("maps NotAllowedError to blocked", async () => {
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    setClipboard({
      write: vi.fn().mockRejectedValue(new DOMException("no", "NotAllowedError")),
    })
    await expect(writeImageToClipboard(Promise.resolve(new Blob()))).resolves.toEqual({
      kind: "error",
      reason: "blocked",
    })
  })
  it("rethrows a rejected blob promise as the underlying error (not swallowed as blocked)", async () => {
    vi.stubGlobal("ClipboardItem", FakeClipboardItem)
    const boom = new Error("boom")
    setClipboard({ write: vi.fn().mockRejectedValue(boom) })
    await expect(writeImageToClipboard(Promise.resolve(new Blob()))).rejects.toBe(boom)
  })
})

describe("copyText", () => {
  it("uses writeText in a secure context", async () => {
    setSecure(true)
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    await expect(copyText("hi")).resolves.toEqual({ kind: "copied" })
    expect(writeText).toHaveBeenCalledWith("hi")
  })
  it("falls back to execCommand and removes the textarea", async () => {
    setSecure(false)
    setClipboard(undefined)
    const exec = vi.fn().mockReturnValue(true)
    ;(document as unknown as Record<string, unknown>).execCommand = exec
    await expect(copyText("hi")).resolves.toEqual({ kind: "copied" })
    expect(exec).toHaveBeenCalledWith("copy")
    expect(document.querySelector("textarea")).toBeNull()
  })
  it("returns unsupported when execCommand returns false", async () => {
    setSecure(false)
    ;(document as unknown as Record<string, unknown>).execCommand = vi.fn().mockReturnValue(false)
    await expect(copyText("hi")).resolves.toEqual({ kind: "error", reason: "unsupported" })
    expect(document.querySelector("textarea")).toBeNull()
  })
  it("returns unsupported when execCommand throws", async () => {
    setSecure(false)
    ;(document as unknown as Record<string, unknown>).execCommand = vi.fn(() => {
      throw new Error("x")
    })
    await expect(copyText("hi")).resolves.toEqual({ kind: "error", reason: "unsupported" })
  })
  it("maps NotAllowedError from writeText to blocked", async () => {
    setSecure(true)
    setClipboard({ writeText: vi.fn().mockRejectedValue(new DOMException("no", "NotAllowedError")) })
    await expect(copyText("hi")).resolves.toEqual({ kind: "error", reason: "blocked" })
  })
})
