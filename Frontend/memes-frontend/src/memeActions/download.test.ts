import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { downloadBlob, openInNewTab } from "./download"

beforeEach(() => {
  vi.useFakeTimers()
  URL.createObjectURL = vi.fn(() => "blob:mock")
  URL.revokeObjectURL = vi.fn()
})
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe("downloadBlob", () => {
  it("clicks one anchor with download and blob href, then removes it", () => {
    const seen: { download?: string | null; href?: string | null; attached?: boolean } = {}
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      seen.download = this.getAttribute("download")
      seen.href = this.getAttribute("href")
      seen.attached = document.body.contains(this)
    })
    downloadBlob(new Blob(["x"]), "cat.png")
    expect(seen).toEqual({ download: "cat.png", href: "blob:mock", attached: true })
    expect(document.querySelector("a[download]")).toBeNull()
  })
  it("revokes the blob url only after 30 seconds", () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {})
    downloadBlob(new Blob(["x"]), "cat.png")
    vi.advanceTimersByTime(29_999)
    expect(URL.revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(2)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock")
  })
})

describe("openInNewTab", () => {
  it("opens with noopener", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null)
    openInNewTab("http://h/a")
    expect(open).toHaveBeenCalledWith("http://h/a", "_blank", "noopener")
  })
})
