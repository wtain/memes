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
    const api = makeMockApi()
    const { result, rerender } = renderHook(({ v }) => useTaxonomy(api, v), { initialProps: { v: null as number | null } })
    await waitFor(() => expect(result.current).not.toBeNull())
    rerender({ v: 3 })
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalledTimes(2))
    rerender({ v: 3 })
    expect(api.getTaxonomy).toHaveBeenCalledTimes(2)
  })

  it("stays null (and does not throw) when the endpoint fails", async () => {
    const api = makeMockApi({ getTaxonomy: vi.fn().mockRejectedValue(new Error("down")) })
    const { result } = renderHook(() => useTaxonomy(api, null))
    await waitFor(() => expect(api.getTaxonomy).toHaveBeenCalled())
    expect(result.current).toBeNull()
  })
})
