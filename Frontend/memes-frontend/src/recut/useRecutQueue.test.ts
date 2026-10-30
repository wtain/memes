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

describe("useRecutQueue adjust", () => {
  const NEW_BOX = { x: 50, y: 40, w: 200, h: 120 }

  async function opened(overrides: Parameters<typeof makeMockApi>[0] = {}) {
    const api = makeMockApi({
      listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["p1", "p2"], 2)),
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("p1")),
      ...overrides,
    })
    const hook = renderHook(() => useRecutQueue(api, "p1"))
    await waitFor(() => expect(hook.result.current.detail?.status).toBe("ready"))
    await waitFor(() => expect(hook.result.current.listStatus).toBe("ready"))
    return { api, ...hook }
  }

  it("swaps in the returned detail and updates the list item", async () => {
    const updated = makeProposalDetail("p1", { box: NEW_BOX, cutImageId: "cut-p1b", detectedBox: { x: 10, y: 20, w: 300, h: 200 } })
    const { api, result } = await opened({ adjustRecutProposal: vi.fn().mockResolvedValue(updated) })

    let outcome: { outcome: string } | undefined
    await act(async () => { outcome = await result.current.adjust(NEW_BOX) })

    expect(outcome).toEqual({ outcome: "done" })
    expect(api.adjustRecutProposal).toHaveBeenCalledWith("p1", NEW_BOX)
    expect(result.current.detail).toMatchObject({ status: "ready", data: { cutImageId: "cut-p1b", box: NEW_BOX } })
    expect(result.current.items[0]).toMatchObject({ id: "p1", box: NEW_BOX, cutImageId: "cut-p1b" })
    expect(result.current.total).toBe(2)
    expect(result.current.adjusting).toBe(false)
  })

  it("treats a 409 as a conflict, reloads the queue and sets the shared decision state", async () => {
    const { api, result } = await opened({
      adjustRecutProposal: vi.fn().mockRejectedValue(new CuratorRequestError(409, "source_changed")),
    })

    let outcome: { outcome: string } | undefined
    await act(async () => { outcome = await result.current.adjust(NEW_BOX) })

    expect(outcome).toEqual({ outcome: "conflict" })
    expect(result.current.decision).toEqual({ status: "conflict", code: "source_changed", redirect: true })
    await waitFor(() => expect(api.listRecutProposals).toHaveBeenCalledTimes(2))
  })

  it("reports a 422 as invalid and leaves the decision state alone", async () => {
    const { result } = await opened({ adjustRecutProposal: vi.fn().mockRejectedValue(new CuratorRequestError(422)) })

    let outcome: { outcome: string } | undefined
    await act(async () => { outcome = await result.current.adjust(NEW_BOX) })

    expect(outcome).toEqual({ outcome: "invalid" })
    expect(result.current.decision).toEqual({ status: "idle" })
  })

  it("reports any other failure as failed and leaves the decision state alone", async () => {
    const { result } = await opened({ adjustRecutProposal: vi.fn().mockRejectedValue(new Error("network")) })

    let outcome: { outcome: string } | undefined
    await act(async () => { outcome = await result.current.adjust(NEW_BOX) })

    expect(outcome).toEqual({ outcome: "failed" })
    expect(result.current.decision).toEqual({ status: "idle" })
    expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "p1" } })
  })

  it("ignores a second request while one is in flight", async () => {
    let resolve: (value: ReturnType<typeof makeProposalDetail>) => void = () => {}
    const adjustRecutProposal = vi.fn(() => new Promise<ReturnType<typeof makeProposalDetail>>((r) => { resolve = r }))
    const { result } = await opened({ adjustRecutProposal })

    let first: Promise<{ outcome: string }> = Promise.resolve({ outcome: "" })
    act(() => { first = result.current.adjust(NEW_BOX) })
    await waitFor(() => expect(result.current.adjusting).toBe(true))
    let second: { outcome: string } | undefined
    await act(async () => { second = await result.current.adjust({ ...NEW_BOX, w: 100 }) })

    expect(second).toEqual({ outcome: "ignored" })
    expect(adjustRecutProposal).toHaveBeenCalledTimes(1)
    await act(async () => { resolve(makeProposalDetail("p1", { box: NEW_BOX })); await first })
  })

  it("does not overwrite the detail of the proposal the reviewer moved to while the adjustment was in flight", async () => {
    let resolveAdjust: (value: ReturnType<typeof makeProposalDetail>) => void = () => {}
    const api = makeMockApi({
      listRecutProposals: vi.fn().mockResolvedValue(makeProposalPage(["p1", "p2"], 2)),
      getRecutProposal: vi.fn((id: string) => Promise.resolve(makeProposalDetail(id))),
      adjustRecutProposal: vi.fn(() => new Promise<ReturnType<typeof makeProposalDetail>>((r) => { resolveAdjust = r })),
    })
    const { result, rerender } = renderHook(({ id }) => useRecutQueue(api, id), { initialProps: { id: "p1" } })
    await waitFor(() => expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "p1" } }))

    let pending: Promise<{ outcome: string }> = Promise.resolve({ outcome: "" })
    act(() => { pending = result.current.adjust(NEW_BOX) })
    rerender({ id: "p2" })
    await waitFor(() => expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "p2" } }))
    await act(async () => { resolveAdjust(makeProposalDetail("p1", { box: NEW_BOX, cutImageId: "cut-p1b" })); await pending })

    expect(result.current.detail).toMatchObject({ status: "ready", data: { id: "p2" } })
    expect(result.current.items.find((item) => item.id === "p1")).toMatchObject({ box: NEW_BOX, cutImageId: "cut-p1b" })
  })

  it("ignores the request when the proposal is no longer waiting", async () => {
    const { api, result } = await opened({
      getRecutProposal: vi.fn().mockResolvedValue(makeProposalDetail("p1", { state: "approved" })),
    })

    let outcome: { outcome: string } | undefined
    await act(async () => { outcome = await result.current.adjust(NEW_BOX) })

    expect(outcome).toEqual({ outcome: "ignored" })
    expect(api.adjustRecutProposal).not.toHaveBeenCalled()
  })
})
