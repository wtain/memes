import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import {
  ImageActionError,
  _resetImageBlobCacheForTests,
  fetchImageBlob,
  isAnimatedGif,
  toPngBlob,
  warmImageBlob,
} from "./imageBlob"

function okResponse(blob: Blob) {
  return { ok: true, blob: () => Promise.resolve(blob) } as unknown as Response
}

beforeEach(() => {
  _resetImageBlobCacheForTests()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("fetchImageBlob", () => {
  it("calls fetch with cors and omitted credentials and returns the blob", async () => {
    const blob = new Blob(["x"], { type: "image/jpeg" })
    const fetchMock = vi.fn().mockResolvedValue(okResponse(blob))
    vi.stubGlobal("fetch", fetchMock)
    await expect(fetchImageBlob("http://h/a")).resolves.toBe(blob)
    expect(fetchMock).toHaveBeenCalledWith("http://h/a", { mode: "cors", credentials: "omit" })
  })
  it("throws network on !ok", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false } as Response))
    await expect(fetchImageBlob("u")).rejects.toMatchObject({ reason: "network" })
  })
  it("throws network on rejection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("x")))
    const err = await fetchImageBlob("u").catch((e) => e)
    expect(err).toBeInstanceOf(ImageActionError)
    expect(err.reason).toBe("network")
  })
})

describe("warmImageBlob", () => {
  it("shares one fetch between concurrent calls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse(new Blob(["x"])))
    vi.stubGlobal("fetch", fetchMock)
    const [a, b] = await Promise.all([warmImageBlob("a", "u"), warmImageBlob("a", "u")])
    expect(a).toBe(b)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
  it("evicts a rejected entry so the next call retries", async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("x"))
      .mockResolvedValue(okResponse(new Blob(["x"])))
    vi.stubGlobal("fetch", fetchMock)
    await expect(warmImageBlob("a", "u")).rejects.toBeInstanceOf(ImageActionError)
    await expect(warmImageBlob("a", "u")).resolves.toBeInstanceOf(Blob)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it("evicts the oldest entry beyond 8", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(okResponse(new Blob(["x"]))))
    vi.stubGlobal("fetch", fetchMock)
    for (let i = 0; i < 9; i++) await warmImageBlob(`id${i}`, "u")
    expect(fetchMock).toHaveBeenCalledTimes(9)
    await warmImageBlob("id8", "u")
    expect(fetchMock).toHaveBeenCalledTimes(9)
    await warmImageBlob("id0", "u")
    expect(fetchMock).toHaveBeenCalledTimes(10)
  })
})

describe("toPngBlob", () => {
  function stubCanvas(toBlobResult: Blob | null) {
    const drawImage = vi.fn()
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D)
    const toBlob = vi
      .spyOn(HTMLCanvasElement.prototype, "toBlob")
      .mockImplementation((cb) => cb(toBlobResult))
    return { drawImage, toBlob }
  }
  function stubBitmap(width: number, height: number) {
    const close = vi.fn()
    vi.stubGlobal("createImageBitmap", vi.fn().mockResolvedValue({ width, height, close }))
    return close
  }

  it("returns the same PNG object without touching canvas", async () => {
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext")
    const png = new Blob(["p"], { type: "image/png" })
    await expect(toPngBlob(png)).resolves.toBe(png)
    expect(getContext).not.toHaveBeenCalled()
  })
  it.each(["image/jpeg", "image/webp"])("converts %s to png", async (type) => {
    const out = new Blob(["o"], { type: "image/png" })
    const { toBlob } = stubCanvas(out)
    const close = stubBitmap(10, 10)
    const result = await toPngBlob(new Blob(["j"], { type }))
    expect(result.type).toBe("image/png")
    expect(toBlob).toHaveBeenCalledWith(expect.any(Function), "image/png")
    expect(close).toHaveBeenCalled()
  })
  it("rejects convert when toBlob yields null", async () => {
    stubCanvas(null)
    const close = stubBitmap(10, 10)
    await expect(toPngBlob(new Blob(["j"], { type: "image/jpeg" }))).rejects.toMatchObject({
      reason: "convert",
    })
    expect(close).toHaveBeenCalled()
  })
  it("rejects too_large over 16 MP and still closes the bitmap", async () => {
    stubCanvas(new Blob(["o"], { type: "image/png" }))
    const close = stubBitmap(4001, 4001)
    await expect(toPngBlob(new Blob(["j"], { type: "image/jpeg" }))).rejects.toMatchObject({
      reason: "too_large",
    })
    expect(close).toHaveBeenCalled()
  })
})

describe("isAnimatedGif", () => {
  const header = [0x47, 0x49, 0x46, 0x38, 0x39, 0x61]
  const gce = [0x21, 0xf9, 0x04, 0, 0, 0, 0, 0]
  it("is false for one frame", () => {
    expect(isAnimatedGif(new Uint8Array([...header, 1, 2, ...gce, 3]))).toBe(false)
  })
  it("is true for two graphic control extensions", () => {
    expect(isAnimatedGif(new Uint8Array([...header, ...gce, 9, ...gce]))).toBe(true)
  })
  it("is false for non-gif bytes", () => {
    expect(isAnimatedGif(new Uint8Array([0x89, 0x50, 0x4e, 0x47, ...gce, ...gce]))).toBe(false)
  })
})
