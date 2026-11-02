import { StrictMode, type ReactNode } from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { makeMockApi } from "../test/mockApi"
import { makeDiscoveryMemes } from "./fixtures"
import { DiscoveryRequestError, type DiscoveryMemes } from "./types"
import { useDiscoveryData } from "./useDiscoveryData"

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => { resolve = r })
  return { promise, resolve }
}

describe("useDiscoveryData", () => {
  it("fetches once with the params and a signal, then returns the data", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useDiscoveryData<DiscoveryMemes>(api, "memes", { limit: 50 }))
    expect(result.current.loading).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual(makeDiscoveryMemes())
    expect(api.getDiscoveryMemes).toHaveBeenCalledTimes(1)
    const [params, signal] = vi.mocked(api.getDiscoveryMemes).mock.calls[0]
    expect(params).toEqual({ limit: 50 })
    expect(signal).toBeInstanceOf(AbortSignal)
  })

  it("shows the server's message for a 422 and a generic one otherwise", async () => {
    const api = makeMockApi({
      getDiscoveryMemes: vi.fn().mockRejectedValue(new DiscoveryRequestError(422, "bad sort")),
      getDiscoveryQueries: vi.fn().mockRejectedValue(new Error("boom")),
    })
    const first = renderHook(() => useDiscoveryData(api, "memes", {}))
    await waitFor(() => expect(first.result.current.error).toBe("bad sort"))
    const second = renderHook(() => useDiscoveryData(api, "queries", {}))
    await waitFor(() => expect(second.result.current.error).toBe("Request failed"))
  })

  it("retry refetches", async () => {
    const api = makeMockApi({
      getDiscoveryMemes: vi.fn().mockRejectedValueOnce(new Error("x")).mockResolvedValue(makeDiscoveryMemes()),
    })
    const { result } = renderHook(() => useDiscoveryData<DiscoveryMemes>(api, "memes", {}))
    await waitFor(() => expect(result.current.error).toBe("Request failed"))
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.data).not.toBeNull())
    expect(api.getDiscoveryMemes).toHaveBeenCalledTimes(2)
  })

  it("refetches when the params change in content, not when only the object identity changes", async () => {
    const api = makeMockApi()
    const { result, rerender } = renderHook(
      ({ params }: { params: object }) => useDiscoveryData(api, "memes", params),
      { initialProps: { params: { limit: 50 } } },
    )
    await waitFor(() => expect(result.current.loading).toBe(false))
    rerender({ params: { limit: 50 } })
    expect(api.getDiscoveryMemes).toHaveBeenCalledTimes(1)
    rerender({ params: { limit: 100 } })
    await waitFor(() => expect(api.getDiscoveryMemes).toHaveBeenCalledTimes(2))
  })

  it("ignores a slow response for older params that resolves after the newer one", async () => {
    const slow = deferred<DiscoveryMemes>()
    const fast = deferred<DiscoveryMemes>()
    const api = makeMockApi({
      getDiscoveryMemes: vi.fn().mockReturnValueOnce(slow.promise).mockReturnValueOnce(fast.promise),
    })
    const { result, rerender } = renderHook(
      ({ params }: { params: object }) => useDiscoveryData<DiscoveryMemes>(api, "memes", params),
      { initialProps: { params: { limit: 50 } } },
    )
    rerender({ params: { limit: 100 } })
    await act(async () => { fast.resolve(makeDiscoveryMemes({ windowDays: 100 })) })
    await act(async () => { slow.resolve(makeDiscoveryMemes({ windowDays: 50 })) })
    await waitFor(() => expect(result.current.data?.windowDays).toBe(100))
  })

  it("survives a double mount under StrictMode and ends with the right state", async () => {
    // Each call gets its own deferred; the last call is the live one, earlier ones are cancelled by the remount.
    const pending: ReturnType<typeof deferred<DiscoveryMemes>>[] = []
    const api = makeMockApi({
      getDiscoveryMemes: vi.fn().mockImplementation(() => {
        const next = deferred<DiscoveryMemes>()
        pending.push(next)
        return next.promise
      }),
    })
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>
    const { result } = renderHook(() => useDiscoveryData<DiscoveryMemes>(api, "memes", {}), { wrapper })
    await act(async () => {
      // resolve the live (last) request first, then the cancelled ones late
      pending[pending.length - 1].resolve(makeDiscoveryMemes({ windowDays: 99 }))
    })
    await act(async () => {
      pending.slice(0, -1).forEach((d) => d.resolve(makeDiscoveryMemes({ windowDays: 1 })))
    })
    await waitFor(() => expect(result.current.data?.windowDays).toBe(99))
    expect(result.current.error).toBeNull()
  })

  it("aborts the request on unmount", async () => {
    const api = makeMockApi({ getDiscoveryMemes: vi.fn().mockReturnValue(new Promise(() => {})) })
    const { unmount } = renderHook(() => useDiscoveryData(api, "memes", {}))
    const signal = vi.mocked(api.getDiscoveryMemes).mock.calls[0][1] as AbortSignal
    expect(signal.aborted).toBe(false)
    unmount()
    expect(signal.aborted).toBe(true)
  })
})
