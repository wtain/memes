import { StrictMode } from "react"
import { act, renderHook, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { MemesApi } from "../api/MemesApi"
import type { Meme } from "../types/generated/all"
import { ToastProvider } from "../ui"
import { ImageActionError } from "./imageBlob"

vi.mock("./imageBlob", async (orig) => ({
  ...(await orig<typeof import("./imageBlob")>()),
  warmImageBlob: vi.fn(),
  toPngBlob: vi.fn(),
  isAnimatedGif: vi.fn(),
}))
vi.mock("./clipboard", () => ({
  canCopyImage: vi.fn(),
  copyText: vi.fn(),
  writeImageToClipboard: vi.fn(),
}))
vi.mock("./download", () => ({ downloadBlob: vi.fn(), openInNewTab: vi.fn() }))
vi.mock("./share", () => ({ canShareFiles: vi.fn(), shareImage: vi.fn() }))

import { canCopyImage, copyText, writeImageToClipboard } from "./clipboard"
import { downloadBlob, openInNewTab } from "./download"
import { isAnimatedGif, toPngBlob, warmImageBlob } from "./imageBlob"
import { canShareFiles, shareImage } from "./share"
import { useMemeActions } from "./useMemeActions"

const meme = { id: "abcdef123456", originalFileName: "cat.png" } as Meme
const api = { getImageUrl: () => "http://api/img/abcdef123456" } as unknown as MemesApi

const mWarm = vi.mocked(warmImageBlob)
const mToPng = vi.mocked(toPngBlob)
const mGif = vi.mocked(isAnimatedGif)
const mCan = vi.mocked(canCopyImage)
const mCopyText = vi.mocked(copyText)
const mWrite = vi.mocked(writeImageToClipboard)
const mDownload = vi.mocked(downloadBlob)
const mOpen = vi.mocked(openInNewTab)
const mCanShare = vi.mocked(canShareFiles)
const mShare = vi.mocked(shareImage)

const png = new Blob(["p"], { type: "image/png" })

function withToast({ children }: { children: React.ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>
}

beforeEach(() => {
  vi.useFakeTimers()
  mCan.mockReturnValue(true)
  mCanShare.mockReturnValue(true)
  mWarm.mockResolvedValue(new Blob(["j"], { type: "image/jpeg" }))
  mToPng.mockResolvedValue(png)
  mGif.mockReturnValue(false)
  mWrite.mockResolvedValue({ kind: "copied" })
  mCopyText.mockResolvedValue({ kind: "copied" })
})
afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

async function runAndSettle(result: { current: ReturnType<typeof useMemeActions> }, kind: Parameters<ReturnType<typeof useMemeActions>["run"]>[0]) {
  await act(async () => {
    result.current.run(kind)
    await vi.advanceTimersByTimeAsync(0)
  })
}

describe("copyImage", () => {
  it("copies, reports success, and clears done after 1.5 s", async () => {
    const { result } = renderHook(() => useMemeActions(meme, api), { wrapper: withToast })
    await runAndSettle(result, "copyImage")
    expect(result.current.message).toBe("Image copied")
    expect(result.current.done).toBe("copyImage")
    expect(result.current.busy).toBeNull()
    expect(statusText()).toBe("Image copied")
    await act(async () => void (await vi.advanceTimersByTimeAsync(1500)))
    expect(result.current.done).toBeNull()
  })

  it("calls clipboard write before the blob promise resolves (Safari)", async () => {
    let release!: (b: Blob) => void
    mWarm.mockReturnValue(new Promise<Blob>((r) => (release = r)))
    mWrite.mockImplementation(() => new Promise(() => {}))
    const { result } = renderHook(() => useMemeActions(meme, api))
    act(() => result.current.run("copyImage"))
    expect(mWrite).toHaveBeenCalledTimes(1)
    expect(mWrite.mock.calls[0][0]).toBeInstanceOf(Promise)
    release(new Blob(["j"], { type: "image/jpeg" }))
  })

  it("passes a promise that resolves to the png blob", async () => {
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "copyImage")
    await expect(mWrite.mock.calls[0][0]).resolves.toBe(png)
  })

  it("copies the page link when image copy is unsupported", async () => {
    mCan.mockReturnValue(false)
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "copyImage")
    expect(mWrite).not.toHaveBeenCalled()
    expect(mCopyText).toHaveBeenCalledWith(new URL("/memes/abcdef123456", window.location.origin).href)
    expect(result.current.message).toContain("Link copied instead")
  })

  it("copies the direct image url for an animated gif", async () => {
    mWarm.mockResolvedValue(new Blob(["g"], { type: "image/gif" }))
    mGif.mockReturnValue(true)
    // A real clipboard.write rejects when the blob promise rejects.
    mWrite.mockImplementation(async (p) => {
      await p
      return { kind: "copied" }
    })
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "copyImage")
    expect(mCopyText).toHaveBeenCalledWith("http://api/img/abcdef123456")
    expect(result.current.message).toMatch(/^Animated GIF cannot be copied as an image/)
  })

  it("shows the blocked text as an alert", async () => {
    mWrite.mockResolvedValue({ kind: "error", reason: "blocked" })
    const { result } = renderHook(() => useMemeActions(meme, api), { wrapper: withToast })
    await runAndSettle(result, "copyImage")
    expect(result.current.message).toBe("Copy was blocked by the browser. Try again or use Download.")
    expect(result.current.done).toBeNull()
    expect(screen.getByRole("alert").textContent).toContain("blocked by the browser")
  })

  it("shows the load text when the fetch fails", async () => {
    mWrite.mockImplementation(async (p) => {
      await p
      return { kind: "copied" }
    })
    mWarm.mockRejectedValue(new ImageActionError("network"))
    const { result } = renderHook(() => useMemeActions(meme, api), { wrapper: withToast })
    await runAndSettle(result, "copyImage")
    expect(result.current.message).toBe("Could not load the image to copy.")
    expect(screen.getByRole("alert").textContent).toContain("Could not load")
  })

  it("ignores a second run while busy", async () => {
    mWrite.mockImplementation(() => new Promise(() => {}))
    const { result } = renderHook(() => useMemeActions(meme, api))
    act(() => {
      result.current.run("copyImage")
      result.current.run("copyImage")
    })
    expect(mWrite).toHaveBeenCalledTimes(1)
    expect(result.current.busy).toBe("copyImage")
  })
})

describe("copyLink", () => {
  it("copies the page url", async () => {
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "copyLink")
    expect(mCopyText).toHaveBeenCalledWith(new URL("/memes/abcdef123456", window.location.origin).href)
    expect(result.current.message).toBe("Link copied")
    expect(result.current.done).toBe("copyLink")
  })
})

describe("download", () => {
  it("downloads with the built filename", async () => {
    const blob = new Blob(["p"], { type: "image/png" })
    mWarm.mockResolvedValue(blob)
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "download")
    expect(mDownload).toHaveBeenCalledWith(blob, "cat.png")
    expect(result.current.done).toBe("download")
  })
  it("opens a new tab when the fetch fails", async () => {
    mWarm.mockRejectedValue(new ImageActionError("network"))
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "download")
    expect(mOpen).toHaveBeenCalledWith("http://api/img/abcdef123456")
    expect(result.current.message).toBe("Could not download directly; the image opened in a new tab.")
  })
})

describe("share", () => {
  it("reports canShare from the util", () => {
    mCanShare.mockReturnValue(false)
    const { result } = renderHook(() => useMemeActions(meme, api))
    expect(result.current.canShare).toBe(false)
  })
  it("shares the file", async () => {
    mShare.mockResolvedValue({ kind: "shared" })
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "share")
    expect(mShare).toHaveBeenCalledWith(expect.any(Blob), "cat.png")
  })
  it("is silent on cancel", async () => {
    mShare.mockResolvedValue({ kind: "cancelled" })
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "share")
    expect(result.current.message).toBe("")
    expect(result.current.done).toBeNull()
  })
  it("reports an error", async () => {
    mShare.mockResolvedValue({ kind: "error" })
    const { result } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "share")
    expect(result.current.message).toBe("Could not share.")
  })
})

describe("lifecycle", () => {
  it("warm() prefetches and swallows rejections", async () => {
    mWarm.mockRejectedValue(new ImageActionError("network"))
    const { result } = renderHook(() => useMemeActions(meme, api))
    await act(async () => result.current.warm())
    expect(mWarm).toHaveBeenCalledWith("abcdef123456", "http://api/img/abcdef123456")
  })
  it("does no work on mount, even under StrictMode", () => {
    renderHook(() => useMemeActions(meme, api), { wrapper: StrictMode })
    expect(mWarm).not.toHaveBeenCalled()
    expect(mWrite).not.toHaveBeenCalled()
  })
  it("leaves no timers after unmount", async () => {
    const { result, unmount } = renderHook(() => useMemeActions(meme, api))
    await runAndSettle(result, "copyLink")
    expect(result.current.done).toBe("copyLink")
    unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})

function statusText(): string {
  return screen.getByRole("status").textContent ?? ""
}
