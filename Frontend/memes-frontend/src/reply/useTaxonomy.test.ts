import { StrictMode } from "react"
import { renderHook, waitFor } from "@testing-library/react"
import { useTaxonomy } from "./useTaxonomy"
import { makeMockApi } from "../test/mockApi"
import { TEST_TAXONOMY } from "./fixtures"

describe("useTaxonomy", () => {
  it("loads the vocabulary once", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useTaxonomy(api, null))
    await waitFor(() => expect(result.current).toEqual(TEST_TAXONOMY))
    expect(api.getTaxonomy).toHaveBeenCalledTimes(1)
  })

  it("does not refetch while the version matches", async () => {
    const api = makeMockApi()
    const { result, rerender } = renderHook(({ v }) => useTaxonomy(api, v), { initialProps: { v: null as number | null } })
    await waitFor(() => expect(result.current).not.toBeNull())
    rerender({ v: 2 })
    expect(api.getTaxonomy).toHaveBeenCalledTimes(1)
  })

  it("refetches once when a response reports another version", async () => {
    const api = makeMockApi({ getTaxonomy: vi.fn().mockResolvedValueOnce(TEST_TAXONOMY).mockResolvedValue({ ...TEST_TAXONOMY, version: 3 }) })
    const { result, rerender } = renderHook(({ v }) => useTaxonomy(api, v), { initialProps: { v: null as number | null } })
    await waitFor(() => expect(result.current).not.toBeNull())
    rerender({ v: 3 })
    await waitFor(() => expect(result.current?.version).toBe(3))
    rerender({ v: 3 })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(api.getTaxonomy).toHaveBeenCalledTimes(2)
  })

  it("does not loop when the backend keeps returning the old version", async () => {
    const api = makeMockApi()
    const { result, rerender } = renderHook(({ v }) => useTaxonomy(api, v), { initialProps: { v: null as number | null } })
    await waitFor(() => expect(result.current).not.toBeNull())
    rerender({ v: 3 })
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalledTimes(2))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(result.current?.version).toBe(2)
    expect(api.getTaxonomy).toHaveBeenCalledTimes(2)
  })

  it("stays null (and does not throw) when the endpoint fails", async () => {
    const api = makeMockApi({ getTaxonomy: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = renderHook(() => useTaxonomy(api, null))
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalled())
    expect(result.current).toBeNull()
  })

  it("loads the vocabulary under StrictMode (effect, cleanup, effect)", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useTaxonomy(api, null), { wrapper: StrictMode })
    await waitFor(() => expect(result.current).toEqual(TEST_TAXONOMY))
    expect((api.getTaxonomy as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(1)
  })

  it("refetches a mismatching version under StrictMode without looping", async () => {
    const api = makeMockApi({ getTaxonomy: vi.fn().mockResolvedValueOnce(TEST_TAXONOMY).mockResolvedValue({ ...TEST_TAXONOMY, version: 3 }) })
    const { result, rerender } = renderHook(({ v }) => useTaxonomy(api, v), { wrapper: StrictMode, initialProps: { v: null as number | null } })
    await waitFor(() => expect(result.current).not.toBeNull())
    rerender({ v: 3 })
    await waitFor(() => expect(result.current?.version).toBe(3))
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect((api.getTaxonomy as ReturnType<typeof vi.fn>).mock.calls.length).toBeLessThanOrEqual(5)
  })

  it("retries a failed load when the retry key changes, once per key and never in a loop", async () => {
    let down = true
    const api = makeMockApi({ getTaxonomy: vi.fn().mockImplementation(() => (down ? Promise.reject(new Error("down")) : Promise.resolve(TEST_TAXONOMY))) })
    const { result, rerender } = renderHook(({ k }) => useTaxonomy(api, null, k), { wrapper: StrictMode, initialProps: { k: 0 } })
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalled())
    expect(result.current).toBeNull()

    down = false
    rerender({ k: 1 })
    await waitFor(() => expect(result.current).toEqual(TEST_TAXONOMY))
    const loaded = (api.getTaxonomy as ReturnType<typeof vi.fn>).mock.calls.length

    rerender({ k: 2 })
    rerender({ k: 1 })
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect((api.getTaxonomy as ReturnType<typeof vi.fn>).mock.calls.length).toBe(loaded)
  })
})
