import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"
import { _resetImageBlobCacheForTests } from "../memeActions/imageBlob"
import { MemeActions } from "./MemeActions"

const meme = { id: "abcdef123456", originalFileName: "cat.png" } as Meme
const api = { getImageUrl: () => "http://api/img/abcdef123456" } as unknown as MemesApi

function setNav(props: Record<string, unknown>) {
  for (const [k, v] of Object.entries(props)) {
    Object.defineProperty(navigator, k, { value: v, configurable: true, writable: true })
  }
}
function stubMatchMedia(coarse: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: coarse && query.includes("coarse"),
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }))
  )
}

beforeEach(() => {
  _resetImageBlobCacheForTests()
  stubMatchMedia(false)
})
afterEach(() => {
  vi.unstubAllGlobals()
  setNav({ share: undefined, canShare: undefined, clipboard: undefined })
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true })
})

describe.each(["bar", "row"] as const)("MemeActions %s", (layout) => {
  it("exposes the named buttons in a Share actions group", () => {
    render(<MemeActions meme={meme} memesApi={api} layout={layout} />)
    const group = screen.getByRole("group", { name: "Share actions" })
    for (const name of ["Copy image", "Download", "Copy link"]) {
      expect(screen.getByRole("button", { name })).toBeTruthy()
      expect(group.contains(screen.getByRole("button", { name }))).toBe(true)
    }
  })
  it("has no Share button without navigator.share", () => {
    render(<MemeActions meme={meme} memesApi={api} layout={layout} />)
    expect(screen.queryByRole("button", { name: "Share" })).toBeNull()
  })
  it("never removes buttons from the tab order", () => {
    render(<MemeActions meme={meme} memesApi={api} layout={layout} />)
    for (const b of screen.getAllByRole("button")) expect(b.getAttribute("tabindex")).not.toBe("-1")
  })
})

describe("Share visibility", () => {
  beforeEach(() => setNav({ share: vi.fn(), canShare: () => true }))
  it("is hidden in the bar on a fine pointer", () => {
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    expect(screen.queryByRole("button", { name: "Share" })).toBeNull()
  })
  it("is shown in the bar on a coarse pointer, first", () => {
    stubMatchMedia(true)
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    const buttons = screen.getAllByRole("button")
    expect(buttons[0].getAttribute("aria-label")).toBe("Share")
  })
  it("is shown in the row on a fine pointer", () => {
    render(<MemeActions meme={meme} memesApi={api} layout="row" />)
    expect(screen.getByRole("button", { name: "Share" })).toBeTruthy()
  })
})

describe("bar layout", () => {
  it("uses icon-only buttons with titles", () => {
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    const b = screen.getByRole("button", { name: "Copy image" })
    expect(b.getAttribute("title")).toBe("Copy image")
    expect(b.textContent).toBe("")
  })
  it("does not let a click reach a parent handler", () => {
    const parent = vi.fn()
    render(
      <div onClick={parent}>
        <MemeActions meme={meme} memesApi={api} layout="bar" />
      </div>
    )
    fireEvent.click(screen.getByRole("button", { name: "Copy link" }))
    expect(parent).not.toHaveBeenCalled()
  })
})

describe("row layout", () => {
  it("shows visible labels and an Open image link", () => {
    render(<MemeActions meme={meme} memesApi={api} layout="row" />)
    expect(screen.getByRole("button", { name: "Copy image" }).textContent).toContain("Copy image")
    const link = screen.getByRole("link", { name: "Open image" })
    expect(link.getAttribute("href")).toBe("http://api/img/abcdef123456")
    expect(link.getAttribute("target")).toBe("_blank")
    expect(link.getAttribute("rel")).toContain("noopener")
  })
})

describe("Copy image click", () => {
  it("writes to the clipboard once", () => {
    vi.stubGlobal("ClipboardItem", class FakeItem {})
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["p"], { type: "image/png" })) })
    )
    const write = vi.fn().mockResolvedValue(undefined)
    setNav({ clipboard: { write } })
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true })
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    fireEvent.click(screen.getByRole("button", { name: "Copy image" }))
    expect(write).toHaveBeenCalledTimes(1)
  })
})

describe("warming", () => {
  function stubFetch() {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, blob: () => Promise.resolve(new Blob(["p"], { type: "image/png" })) })
    vi.stubGlobal("fetch", fetchMock)
    return fetchMock
  }
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.useRealTimers()
    setNav({ connection: undefined })
  })

  it("does not prefetch on a quick pass-over", () => {
    const fetchMock = stubFetch()
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    const group = screen.getByRole("group", { name: "Share actions" })
    fireEvent.pointerEnter(group)
    act(() => void vi.advanceTimersByTime(100))
    fireEvent.pointerLeave(group)
    act(() => void vi.advanceTimersByTime(1000))
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it("prefetches once after the pointer dwells", () => {
    const fetchMock = stubFetch()
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    fireEvent.pointerEnter(screen.getByRole("group", { name: "Share actions" }))
    act(() => void vi.advanceTimersByTime(250))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it("prefetches after focus dwell and cancels on blur", () => {
    const fetchMock = stubFetch()
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    const button = screen.getByRole("button", { name: "Download" })
    fireEvent.focus(button)
    fireEvent.blur(button)
    act(() => void vi.advanceTimersByTime(500))
    expect(fetchMock).not.toHaveBeenCalled()
    fireEvent.focus(button)
    act(() => void vi.advanceTimersByTime(250))
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it("does not prefetch on touchstart", () => {
    const fetchMock = stubFetch()
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    fireEvent.touchStart(screen.getByRole("group", { name: "Share actions" }))
    act(() => void vi.advanceTimersByTime(1000))
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it("does not prefetch when saveData is on", () => {
    setNav({ connection: { saveData: true } })
    const fetchMock = stubFetch()
    render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    fireEvent.pointerEnter(screen.getByRole("group", { name: "Share actions" }))
    act(() => void vi.advanceTimersByTime(1000))
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it("leaves no timer after unmount", () => {
    stubFetch()
    const { unmount } = render(<MemeActions meme={meme} memesApi={api} layout="bar" />)
    fireEvent.pointerEnter(screen.getByRole("group", { name: "Share actions" }))
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
