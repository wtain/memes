import { act, renderHook, waitFor } from "@testing-library/react"
import { StrictMode } from "react"
import { useProfileCoverage } from "./useProfileCoverage"
import { makeCoverage, makeCoverageImages } from "./fixtures"
import { makeMockApi } from "../test/mockApi"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe("useProfileCoverage", () => {
  it("loads the coverage and the first category", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.coverage).toEqual(makeCoverage()))
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(result.current.category).toBe("without_profile")
    expect(api.getProfileCoverageImages).toHaveBeenCalledWith("without_profile")
  })

  it("loads the coverage once under StrictMode", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileCoverage(api), { wrapper: StrictMode })
    await waitFor(() => expect(result.current.coverage).not.toBeNull())
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    expect(api.getProfileCoverage).toHaveBeenCalledTimes(1)
    expect(api.getProfileCoverageImages).toHaveBeenCalledTimes(1)
  })

  it("select requests page one of the new category and drops the old items", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    const gate = deferred<ReturnType<typeof makeCoverageImages>>()
    vi.mocked(api.getProfileCoverageImages).mockReturnValueOnce(gate.promise)
    act(() => result.current.select("failed"))
    expect(api.getProfileCoverageImages).toHaveBeenLastCalledWith("failed", undefined)
    expect(result.current.items).toEqual([])
    expect(result.current.loading).toBe(true)
    await act(async () => gate.resolve(makeCoverageImages(["f-1", "f-2"])))
    expect(result.current.items.map((i) => i.id)).toEqual(["f-1", "f-2"])
    expect(result.current.total).toBe(2)
    expect(result.current.loading).toBe(false)
  })

  it("loadMore requests the next page with the cursor and appends; hasMore follows nextCursor", async () => {
    const api = makeMockApi({
      getProfileCoverageImages: vi.fn()
        .mockResolvedValueOnce(makeCoverageImages(["a"], "c1"))
        .mockResolvedValueOnce(makeCoverageImages(["b"], null)),
    })
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(["a", "b"]))
    expect(api.getProfileCoverageImages).toHaveBeenLastCalledWith("without_profile", "c1")
    expect(result.current.hasMore).toBe(false)
  })

  it("ignores loadMore while loading", async () => {
    const gate = deferred<ReturnType<typeof makeCoverageImages>>()
    const api = makeMockApi({
      getProfileCoverageImages: vi.fn()
        .mockResolvedValueOnce(makeCoverageImages(["a"], "c1"))
        .mockReturnValueOnce(gate.promise),
    })
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    act(() => result.current.loadMore())
    expect(api.getProfileCoverageImages).toHaveBeenCalledTimes(2)
    await act(async () => gate.resolve(makeCoverageImages(["b"])))
  })

  it("ignores a result for a category that is no longer selected", async () => {
    const slow = deferred<ReturnType<typeof makeCoverageImages>>()
    const api = makeMockApi()
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.items).toHaveLength(1))
    vi.mocked(api.getProfileCoverageImages)
      .mockReturnValueOnce(slow.promise)
      .mockResolvedValueOnce(makeCoverageImages(["n-1"]))
    act(() => result.current.select("failed"))
    act(() => result.current.select("needs_review"))
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(["n-1"]))
    await act(async () => slow.resolve(makeCoverageImages(["late"])))
    expect(result.current.items.map((i) => i.id)).toEqual(["n-1"])
    expect(result.current.category).toBe("needs_review")
  })

  it("sets failed when the coverage call fails, and reload retries", async () => {
    const api = makeMockApi({
      getProfileCoverage: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(makeCoverage()),
    })
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.failed).toBe(true))
    act(() => result.current.reload())
    await waitFor(() => expect(result.current.coverage).not.toBeNull())
    expect(result.current.failed).toBe(false)
  })

  it("sets listFailed on a failed list call and keeps earlier items", async () => {
    const api = makeMockApi({
      getProfileCoverageImages: vi.fn()
        .mockResolvedValueOnce(makeCoverageImages(["a"], "c1"))
        .mockRejectedValueOnce(new Error("x")),
    })
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.hasMore).toBe(true))
    act(() => result.current.loadMore())
    await waitFor(() => expect(result.current.listFailed).toBe(true))
    expect(result.current.items.map((i) => i.id)).toEqual(["a"])
    expect(result.current.loading).toBe(false)
  })

  it("selecting the current category again retries a failed list load", async () => {
    const api = makeMockApi({
      getProfileCoverageImages: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(makeCoverageImages(["a"])),
    })
    const { result } = renderHook(() => useProfileCoverage(api))
    await waitFor(() => expect(result.current.listFailed).toBe(true))
    act(() => result.current.select("without_profile"))
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(["a"]))
    expect(result.current.listFailed).toBe(false)
    expect(api.getProfileCoverageImages).toHaveBeenCalledTimes(2)
    act(() => result.current.select("without_profile"))
    expect(api.getProfileCoverageImages).toHaveBeenCalledTimes(2)
  })
})
