import { act, renderHook, waitFor } from "@testing-library/react"
import { CuratorRequestError } from "../api/errors"
import { makeMockApi } from "../test/mockApi"
import { makeProposalDetail, makeProposalPage } from "./fixtures"
import { PAGE_SIZE, useRecutQueue } from "./useRecutQueue"

describe("useRecutQueue", () => {
  it("loads the first page", async () => {
    const api = makeMockApi({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a", "b"], 2)) })
    const { result } = renderHook(() => useRecutQueue(api, null))

    expect(result.current.listStatus).toBe("loading")
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    expect(result.current.items.map((item) => item.id)).toEqual(["a", "b"])
    expect(result.current.total).toBe(2)
    expect(api.listRecutProposals).toHaveBeenCalledWith(0, PAGE_SIZE)
    expect(result.current.detail).toBeNull()
  })

  it("reports an empty queue", async () => {
    const api = makeMockApi()
    const { result } = renderHook(() => useRecutQueue(api, null))

    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    expect(result.current.items).toEqual([])
    expect(result.current.total).toBe(0)
  })

  it("reports a failed list and retries on reload", async () => {
    const list = vi.fn().mockRejectedValueOnce(new CuratorRequestError(500)).mockResolvedValue(makeProposalPage(["a"]))
    const api = makeMockApi({ listRecutProposals: list })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("failed"))

    act(() => result.current.reload())

    await waitFor(() => expect(result.current.listStatus).toBe("ready"))
    expect(result.current.items).toHaveLength(1)
  })

  it("loads the detail of the open proposal and follows it when the id changes", async () => {
    const get = vi.fn((id: string) => Promise.resolve(makeProposalDetail(id, { nextId: "next" })))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "a" as string | null } })

    await waitFor(() => expect(result.current.detail?.status).toBe("ready"))
    expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "a", nextId: "next" } })

    rerender({ id: "b" })
    expect(result.current.detail).toBeNull()
    await waitFor(() => expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "b" } }))
  })

  it("marks a 404 as missing and any other failure as failed", async () => {
    const get = vi.fn().mockRejectedValue(new CuratorRequestError(404))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "gone" as string | null } })
    await waitFor(() => expect(result.current.detail).toEqual({ status: "missing" }))

    get.mockRejectedValue(new CuratorRequestError(500))
    rerender({ id: "broken" })
    await waitFor(() => expect(result.current.detail).toEqual({ status: "failed" }))
  })

  it("ignores a detail that arrives after the open proposal changed", async () => {
    let resolveFirst: (value: ReturnType<typeof makeProposalDetail>) => void = () => {}
    const get = vi.fn((id: string) => id === "a"
      ? new Promise<ReturnType<typeof makeProposalDetail>>((resolve) => { resolveFirst = resolve })
      : Promise.resolve(makeProposalDetail(id)))
    const api = makeMockApi({ getRecutProposal: get })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "a" as string | null } })

    rerender({ id: "b" })
    await waitFor(() => expect(result.current.detail).toMatchObject({ data: { id: "b" } }))
    await act(async () => { resolveFirst(makeProposalDetail("a")) })

    expect(result.current.detail).toMatchObject({ data: { id: "b" } })
  })

  it("loads the next page when asked and appends it without repeating a row", async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(makeProposalPage(["a", "b"], 4))
      .mockResolvedValueOnce(makeProposalPage(["b", "c"], 4))
    const api = makeMockApi({ listRecutProposals: list })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    act(() => result.current.loadMore())

    await waitFor(() => expect(result.current.items.map((item) => item.id)).toEqual(["a", "b", "c"]))
    expect(list).toHaveBeenLastCalledWith(2, PAGE_SIZE)
  })

  it("does not ask for more when everything is loaded", async () => {
    const api = makeMockApi({ listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["a"], 1)) })
    const { result } = renderHook(() => useRecutQueue(api, null))
    await waitFor(() => expect(result.current.listStatus).toBe("ready"))

    act(() => result.current.loadMore())

    expect(api.listRecutProposals).toHaveBeenCalledTimes(1)
  })
})
