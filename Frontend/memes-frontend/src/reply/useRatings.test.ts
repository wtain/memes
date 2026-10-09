import { act, renderHook, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import { useRatings } from "./useRatings"
import { makeMockApi } from "../test/mockApi"
import type { RatingContext } from "./types"

const HASH = "a".repeat(64)
const CONTEXT: RatingContext = {
  text: "hello", rank: 2, reading: "parsed", parserVersion: "p3-x", taxonomyVersion: 2, degraded: false,
  fallback: "none", language: "en",
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe("useRatings", () => {
  it("loads the grades of the message and reports whether any exist", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades).toEqual({ a: 1 }))
    expect(result.current.hasRatings).toBe(true)
    expect(api.getRatings).toHaveBeenCalledWith(HASH)
  })

  it("does nothing without a query hash", () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRatings(api, null))
    act(() => result.current.rate(CONTEXT, "a", 2))
    expect(api.getRatings).not.toHaveBeenCalled()
    expect(api.putRating).not.toHaveBeenCalled()
    expect(result.current.hasRatings).toBe(false)
  })

  it("a failed load is silent and leaves the bar empty", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())
    expect(result.current.grades).toEqual({})
  })

  it("rates optimistically, sends the full request and announces 'saved'", async () => {
    const put = deferred<unknown>()
    const api = makeMockApi({ putRating: vi.fn().mockReturnValue(put.promise) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => result.current.rate(CONTEXT, "a", 2))
    expect(result.current.grades.a).toBe(2)                      // optimistic, before the server answered
    await act(async () => { put.resolve({ queryHash: HASH, imageId: "a", grade: 2 }) })

    expect(api.putRating).toHaveBeenCalledWith({ ...CONTEXT, imageId: "a", grade: 2 })
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "saved" })
    expect(result.current.grades.a).toBe(2)
  })

  it("clearing deletes the rating and announces 'cleared'", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 2 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades.a).toBe(2))

    await act(async () => { result.current.rate(CONTEXT, "a", null) })

    expect(api.deleteRating).toHaveBeenCalledWith(HASH, "a")
    expect(result.current.grades.a).toBeUndefined()
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "cleared" })
  })

  it("a failed save rolls back to the last confirmed grade and reports 'failed'", async () => {
    const api = makeMockApi({
      getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }),
      putRating: vi.fn().mockRejectedValue(new Error("down")),
    })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(result.current.grades.a).toBe(1))

    await act(async () => { result.current.rate(CONTEXT, "a", 2) })

    expect(result.current.grades.a).toBe(1)
    expect(result.current.lastEvent).toEqual({ imageId: "a", kind: "failed" })
  })

  it("rapid clicks on one image are sent in order and the last click wins", async () => {
    const first = deferred<unknown>()
    const put = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ queryHash: HASH, imageId: "a", grade: 0 })
    const api = makeMockApi({ putRating: put })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => { result.current.rate(CONTEXT, "a", 2); result.current.rate(CONTEXT, "a", 0) })
    await act(async () => {})                                     // let the first request start
    expect(put).toHaveBeenCalledTimes(1)                          // the second waits for the first
    await act(async () => { first.resolve({ queryHash: HASH, imageId: "a", grade: 2 }) })

    expect(put.mock.calls.map((call) => call[0].grade)).toEqual([2, 0])
    expect(result.current.grades.a).toBe(0)
  })

  it("a failure of an earlier click does not undo a later successful one", async () => {
    const first = deferred<unknown>()
    const put = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue({ queryHash: HASH, imageId: "a", grade: 0 })
    const api = makeMockApi({ putRating: put })
    const { result } = renderHook(() => useRatings(api, HASH))
    await waitFor(() => expect(api.getRatings).toHaveBeenCalled())

    act(() => { result.current.rate(CONTEXT, "a", 2); result.current.rate(CONTEXT, "a", 0) })
    await act(async () => { first.reject(new Error("down")) })

    expect(result.current.grades.a).toBe(0)
  })

  it("a new message starts with an empty map and loads its own grades", async () => {
    const api = makeMockApi({
      getRatings: vi.fn().mockImplementation(async (hash: string) => ({
        queryHash: hash, ratings: hash === HASH ? [{ imageId: "a", grade: 2 }] : [],
      })),
    })
    const { result, rerender } = renderHook(({ hash }) => useRatings(api, hash), { initialProps: { hash: HASH as string | null } })
    await waitFor(() => expect(result.current.grades.a).toBe(2))

    rerender({ hash: "b".repeat(64) })

    expect(result.current.grades).toEqual({})
    await waitFor(() => expect(api.getRatings).toHaveBeenCalledWith("b".repeat(64)))
  })

  it("forgetting the text calls the API and reports success or failure", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRatings(api, HASH))
    let ok = false
    await act(async () => { ok = await result.current.forgetText() })
    expect(ok).toBe(true)
    expect(api.forgetRatingText).toHaveBeenCalledWith(HASH)

    ;(api.forgetRatingText as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("down"))
    await act(async () => { ok = await result.current.forgetText() })
    expect(ok).toBe(false)
  })

  it("loads the grades under StrictMode", async () => {
    const api = makeMockApi({ getRatings: vi.fn().mockResolvedValue({ queryHash: HASH, ratings: [{ imageId: "a", grade: 1 }] }) })
    const { result } = renderHook(() => useRatings(api, HASH), { wrapper: StrictMode })
    await waitFor(() => expect(result.current.grades).toEqual({ a: 1 }))
  })
})
