import { StrictMode } from "react"
import { act, renderHook } from "@testing-library/react"
import { useMessageSearch } from "./useMessageSearch"
import { makeMockApi } from "../test/mockApi"
import { makeProfile, makeResponse, makeResult } from "./fixtures"
import { addValue, removeValue, setSituation } from "./profile"
import { MessageSearchFailedError, MessageSearchValidationError } from "../api/errors"

function setup(overrides = {}, options = {}) {
  const api = makeMockApi({
    searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })),
    ...overrides,
  })
  const hook = renderHook(() => useMessageSearch(api, { debounceMs: 400, storage: null, ...options }))
  return { api, ...hook }
}

async function find(result: ReturnType<typeof setup>["result"], text = "hello") {
  act(() => result.current.setText(text))
  await act(async () => { result.current.find() })
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

describe("useMessageSearch", () => {
  it("retry after a failed refine re-sends the same edited profile and filters", async () => {
    const { api, result } = setup()
    await find(result)
    const calls = api.searchMemeFromText as ReturnType<typeof vi.fn>
    calls.mockRejectedValueOnce(new MessageSearchFailedError(500))
    await act(async () => { result.current.setFilter("emotion", ["joy"]) })
    expect(result.current.state.status).toBe("error")

    await act(async () => { result.current.retry() })

    expect(calls).toHaveBeenCalledTimes(3)
    const request = calls.mock.calls[2][0]
    expect(request.filters).toEqual({ emotion: ["joy"] })
    expect(request.profile).toBeDefined()
    expect(request).toEqual(calls.mock.calls[1][0])
  })

  it("retry after a failed first search re-sends the text only", async () => {
    const { api, result } = setup()
    const calls = api.searchMemeFromText as ReturnType<typeof vi.fn>
    calls.mockRejectedValueOnce(new MessageSearchFailedError(500))
    await find(result)
    expect(result.current.state.status).toBe("error")

    await act(async () => { result.current.retry() })

    expect(calls).toHaveBeenCalledTimes(2)
    expect(calls.mock.calls[1][0]).toEqual({ text: "hello", limit: 20 })
  })

  it("find sends only the text and stores the reading and results", async () => {
    const { api, result } = setup()
    await find(result)

    expect(api.searchMemeFromText).toHaveBeenCalledTimes(1)
    expect((api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls[0][0]).toEqual({ text: "hello", limit: 20 })
    expect(result.current.state.status).toBe("ready")
    expect(result.current.state.items).toHaveLength(1)
    expect(result.current.activeProfile).not.toBeNull()
  })

  it("does nothing for whitespace-only input", async () => {
    const { api, result } = setup()
    await find(result, "   ")
    expect(api.searchMemeFromText).not.toHaveBeenCalled()
    expect(result.current.state.status).toBe("idle")
  })

  it("chip edits are debounced into one refine request that carries the edited profile", async () => {
    const { api, result } = setup()
    await find(result)
    const calls = api.searchMemeFromText as ReturnType<typeof vi.fn>

    act(() => result.current.editProfile((p) => addValue(p, "emotion", "joy", 3)))
    act(() => result.current.editProfile((p) => removeValue(p, "stance", "sarcasm")))
    expect(calls).toHaveBeenCalledTimes(1)

    await act(async () => { await vi.advanceTimersByTimeAsync(400) })

    expect(calls).toHaveBeenCalledTimes(2)
    const request = calls.mock.calls[1][0]
    expect(request.text).toBe("hello")
    expect(request.profile.emotion.map((v: { key: string }) => v.key)).toEqual(["annoyance", "joy"])
    expect(request.profile.stance).toEqual([])
    expect(request.profile.degraded).toBe(false)
  })

  it("an edit that changes nothing sends nothing", async () => {
    const { api, result } = setup()
    await find(result)
    act(() => result.current.editProfile((p) => setSituation(p, p.situation as string)))
    await act(async () => { await vi.advanceTimersByTimeAsync(1000) })
    expect(api.searchMemeFromText).toHaveBeenCalledTimes(1)
  })

  it("filters re-run immediately with the profile in use", async () => {
    const { api, result } = setup()
    await find(result)
    await act(async () => { result.current.setFilter("tone", ["wholesome"]) })

    const request = (api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls[1][0]
    expect(request.filters).toEqual({ tone: ["wholesome"] })
    expect(request.profile).toEqual(result.current.state.originalProfile)
  })

  it("reset clears the edit and re-runs with the original reading", async () => {
    const { api, result } = setup()
    await find(result)
    act(() => result.current.editProfile((p) => addValue(p, "emotion", "joy", 3)))
    await act(async () => { await vi.advanceTimersByTimeAsync(400) })

    await act(async () => { result.current.resetProfile() })

    const calls = (api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[calls.length - 1][0].profile).toEqual(result.current.state.originalProfile)
    expect(result.current.state.editedProfile).toBeNull()
  })

  it("show more replays the first request with the cursor and appends", async () => {
    const first = makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })
    const second = makeResponse({ items: [makeResult("b")], hasNext: false })
    const search = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    const { result } = setup({ searchMemeFromText: search })
    await find(result)

    await act(async () => { result.current.loadMore() })

    expect(search.mock.calls[1][0]).toEqual({ text: "hello", limit: 20, cursor: "c1" })
    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["a", "b"])
    expect(result.current.state.hasNext).toBe(false)
  })

  it("show more is ignored while a refine is in flight", async () => {
    const first = makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })
    const search = vi.fn().mockResolvedValueOnce(first).mockImplementationOnce(() => new Promise(() => {}))
    const { result } = setup({ searchMemeFromText: search })
    await find(result)
    await act(async () => { result.current.setFilter("tone", ["x"]) })
    expect(result.current.state.status).toBe("refining")

    await act(async () => { result.current.loadMore() })

    expect(search).toHaveBeenCalledTimes(2)
    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["a"])
  })

  it("show more is ignored while an edit's debounce is pending", async () => {
    const first = makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })
    const search = vi.fn().mockResolvedValue(first)
    const { result } = setup({ searchMemeFromText: search })
    await find(result)
    act(() => result.current.editProfile((p) => addValue(p, "emotion", "joy", 3)))

    await act(async () => { result.current.loadMore() })

    expect(search).toHaveBeenCalledTimes(1)
  })

  it("clear filters re-runs with the original reading and no filters", async () => {
    const { api, result } = setup()
    await find(result)
    await act(async () => { result.current.setFilter("tone", ["wholesome"]) })
    await act(async () => { result.current.clearFilters() })

    const calls = (api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls
    expect(calls).toHaveLength(3)
    expect(calls[2][0].filters).toBeUndefined()
    expect(calls[2][0].profile).toEqual(result.current.state.originalProfile)
    expect(result.current.state.filters).toEqual({ emotion: [], stance: [], topic: [], tone: [] })
  })

  it("show more replaces the list when the server restarted it", async () => {
    const first = makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" })
    const restarted = makeResponse({ items: [makeResult("a"), makeResult("b")], hasNext: false })
    const search = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(restarted)
    const { result } = setup({ searchMemeFromText: search })
    await find(result)

    await act(async () => { result.current.loadMore() })

    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["a", "b"])
  })

  it("drops a response that arrives after a newer request began", async () => {
    let resolveFirst: (value: unknown) => void = () => {}
    const search = vi.fn()
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("new")] }))
    const { result } = setup({ searchMemeFromText: search })
    act(() => result.current.setText("one"))
    await act(async () => { result.current.find() })
    act(() => result.current.setText("two"))
    await act(async () => { result.current.find() })

    await act(async () => { resolveFirst(makeResponse({ items: [makeResult("old")] })) })

    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["new"])
    const signal = search.mock.calls[0][1] as AbortSignal
    expect(signal.aborted).toBe(true)
  })

  it("maps a validation error and a generic failure to error kinds, keeping the results", async () => {
    const search = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")] }))
      .mockRejectedValueOnce(new MessageSearchValidationError(["bad"]))
      .mockRejectedValueOnce(new MessageSearchFailedError(500))
    const { result } = setup({ searchMemeFromText: search })
    await find(result)

    await act(async () => { result.current.setFilter("tone", ["x"]) })
    expect(result.current.state).toMatchObject({ status: "error", error: "validation" })
    expect(result.current.state.items).toHaveLength(1)

    await act(async () => { result.current.setFilter("tone", ["y"]) })
    expect(result.current.state).toMatchObject({ status: "error", error: "failed" })
  })

  it("restores a saved search and re-runs retrieval with the saved reading", async () => {
    const profile = makeProfile()
    const store: Record<string, string> = {
      "reply.v1": JSON.stringify({ text: "hello", submitted: "hello", originalProfile: profile, editedProfile: null, filters: {} }),
    }
    const storage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v } } as unknown as Storage
    const { api, result } = setup({}, { storage })

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })

    const calls = (api.searchMemeFromText as ReturnType<typeof vi.fn>).mock.calls
    expect(calls).toHaveLength(1)
    expect(calls[0][0]).toMatchObject({ text: "hello", profile })
    expect(result.current.state.text).toBe("hello")
  })

  it("persists the search to storage as it changes", async () => {
    const store: Record<string, string> = {}
    const storage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v } } as unknown as Storage
    const { result } = setup({}, { storage })
    await find(result)
    expect(JSON.parse(store["reply.v1"])).toMatchObject({ text: "hello", submitted: "hello" })
  })

  it("restores a saved search under StrictMode and ends with the results applied", async () => {
    const profile = makeProfile()
    const store: Record<string, string> = {
      "reply.v1": JSON.stringify({ text: "hello", submitted: "hello", originalProfile: profile, editedProfile: null, filters: {} }),
    }
    const storage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v } } as unknown as Storage
    const api = makeMockApi({ searchMemeFromText: vi.fn().mockResolvedValue(makeResponse({ items: [makeResult("a")] })) })
    const { result } = renderHook(() => useMessageSearch(api, { debounceMs: 400, storage }), { wrapper: StrictMode })

    await act(async () => { await vi.advanceTimersByTimeAsync(50) })

    expect(result.current.state.status).toBe("ready")
    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["a"])
  })

  it("a 404 or 405 from the endpoint makes message search unavailable instead of an error", async () => {
    for (const status of [404, 405]) {
      const { result } = setup({ searchMemeFromText: vi.fn().mockRejectedValue(new MessageSearchFailedError(status)) })
      await find(result)
      expect(result.current.state).toMatchObject({ status: "unavailable", error: null })
    }
  })

  it("other HTTP failures stay a retryable error", async () => {
    const { result } = setup({ searchMemeFromText: vi.fn().mockRejectedValue(new MessageSearchFailedError(503)) })
    await find(result)
    expect(result.current.state).toMatchObject({ status: "error", error: "failed" })
  })

  it("retry after a rejected (422) reading starts a new reading from the text, dropping profile and filters", async () => {
    const search = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")] }))
      .mockRejectedValueOnce(new MessageSearchValidationError(["bad"]))
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("n")] }))
    const { result } = setup({ searchMemeFromText: search })
    await find(result)
    await act(async () => { result.current.setFilter("tone", ["x"]) })
    expect(result.current.state.error).toBe("validation")

    await act(async () => { result.current.retry() })

    expect(search.mock.calls[2][0]).toEqual({ text: "hello", limit: 20 })
    expect(result.current.state.filters).toEqual({ emotion: [], stance: [], topic: [], tone: [] })
    expect(result.current.state.items.map((r) => r.meme.id)).toEqual(["n"])
  })

  it("show more is ignored while the list is in error", async () => {
    const search = vi.fn()
      .mockResolvedValueOnce(makeResponse({ items: [makeResult("a")], hasNext: true, nextCursor: "c1" }))
      .mockRejectedValueOnce(new MessageSearchFailedError(500))
    const { result } = setup({ searchMemeFromText: search })
    await find(result)
    await act(async () => { result.current.setFilter("tone", ["x"]) })
    expect(result.current.state.status).toBe("error")

    await act(async () => { result.current.loadMore() })

    expect(search).toHaveBeenCalledTimes(2)
  })
})
