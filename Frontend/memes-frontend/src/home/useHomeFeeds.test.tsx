import { StrictMode } from "react"
import { act, renderHook, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { makeMockApi } from "../test/mockApi"
import type { Meme } from "../types/generated/all"
import { MIN_TRENDING, RAIL_SIZE, useHomeFeeds } from "./useHomeFeeds"

function meme(id: string, extra: Partial<Meme> = {}): Meme {
  return { id, imageUrl: `/images/${id}.jpg`, text: [], tags: [], flagged: false, ...extra }
}
const memes = (n: number, prefix = "m") => Array.from({ length: n }, (_, i) => meme(`${prefix}${i}`))
const trending = (items: Meme[]) => ({
  items: items.map(m => ({ meme: m, popularity: 3 })),
  hasNext: false,
})

describe("useHomeFeeds", () => {
  it("requests the recency feed and the trending feed with the exact parameters", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: memes(2), hasNext: false }),
      getTrendingMemes: vi.fn().mockResolvedValue(trending([])),
    })
    const { result } = renderHook(() => useHomeFeeds(api))
    expect(result.current.recent).toEqual({ status: "loading", items: [] })
    expect(result.current.trending).toEqual({ status: "loading", items: [] })
    await waitFor(() => expect(result.current.recent.status).toBe("ready"))
    expect(api.searchMemes).toHaveBeenCalledWith({ limit: 12, record: false })
    expect(vi.mocked(api.searchMemes).mock.calls[0][0]).not.toHaveProperty("query")
    expect(api.getTrendingMemes).toHaveBeenCalledWith(12, undefined, "all")
    expect(RAIL_SIZE).toBe(12)
    expect(MIN_TRENDING).toBe(3)
  })

  it("drops flagged memes from recent and caps at twelve", async () => {
    const items = [meme("f1", { flagged: true }), ...memes(14), meme("f2", { flagged: true })]
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue({ items, hasNext: true }) })
    const { result } = renderHook(() => useHomeFeeds(api))
    await waitFor(() => expect(result.current.recent.status).toBe("ready"))
    const ids = result.current.recent.items.map(m => m.id)
    expect(ids).toHaveLength(12)
    expect(ids).not.toContain("f1")
    expect(ids).not.toContain("f2")
  })

  it("dedupes trending against recent and hides a rail with fewer than three left", async () => {
    const recent = memes(5)
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: recent, hasNext: false }),
      getTrendingMemes: vi.fn().mockResolvedValue(trending([recent[0], recent[1], ...memes(2, "t")])),
    })
    const { result } = renderHook(() => useHomeFeeds(api))
    await waitFor(() => expect(result.current.trending.status).toBe("ready"))
    expect(result.current.trending.items).toEqual([])
  })

  it("keeps trending when three distinct memes remain", async () => {
    const recent = memes(5)
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: recent, hasNext: false }),
      getTrendingMemes: vi.fn().mockResolvedValue(trending([recent[0], ...memes(3, "t")])),
    })
    const { result } = renderHook(() => useHomeFeeds(api))
    await waitFor(() => expect(result.current.trending.status).toBe("ready"))
    expect(result.current.trending.items.map(m => m.id)).toEqual(["t0", "t1", "t2"])
  })

  it("keeps trending loading until recent has settled", async () => {
    let resolveRecent: (v: { items: Meme[]; hasNext: boolean }) => void = () => {}
    const api = makeMockApi({
      searchMemes: vi.fn().mockReturnValue(new Promise(r => { resolveRecent = r })),
      getTrendingMemes: vi.fn().mockResolvedValue(trending(memes(4, "t"))),
    })
    const { result } = renderHook(() => useHomeFeeds(api))
    await act(async () => { await Promise.resolve() })
    expect(api.getTrendingMemes).toHaveBeenCalled()
    expect(result.current.trending.status).toBe("loading")
    await act(async () => { resolveRecent({ items: memes(1), hasNext: false }) })
    expect(result.current.trending.status).toBe("ready")
    expect(result.current.trending.items).toHaveLength(4)
  })

  it("does not dedupe trending when recent failed", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockRejectedValue(new Error("down")),
      getTrendingMemes: vi.fn().mockResolvedValue(trending(memes(3, "t"))),
    })
    const { result } = renderHook(() => useHomeFeeds(api))
    await waitFor(() => expect(result.current.recent.status).toBe("error"))
    await waitFor(() => expect(result.current.trending.status).toBe("ready"))
    expect(result.current.trending.items).toHaveLength(3)
  })

  it("retries only the failed feed", async () => {
    const searchMemes = vi
      .fn()
      .mockRejectedValueOnce(new Error("down"))
      .mockResolvedValue({ items: memes(2), hasNext: false })
    const api = makeMockApi({ searchMemes, getTrendingMemes: vi.fn().mockResolvedValue(trending([])) })
    const { result } = renderHook(() => useHomeFeeds(api))
    await waitFor(() => expect(result.current.recent.status).toBe("error"))
    expect(result.current.trending.status).toBe("ready")
    act(() => result.current.retry("recent"))
    expect(result.current.recent.status).toBe("loading")
    await waitFor(() => expect(result.current.recent.status).toBe("ready"))
    expect(searchMemes).toHaveBeenCalledTimes(2)
    expect(api.getTrendingMemes).toHaveBeenCalledTimes(1)
  })

  it("ignores a slow response that a retry has superseded", async () => {
    const resolvers: Array<(v: { items: Meme[]; hasNext: boolean }) => void> = []
    const searchMemes = vi.fn().mockImplementation(() => new Promise(r => { resolvers.push(r) }))
    const api = makeMockApi({ searchMemes })
    const { result } = renderHook(() => useHomeFeeds(api))
    await act(async () => { await Promise.resolve() })
    act(() => result.current.retry("recent"))
    expect(resolvers).toHaveLength(2)
    await act(async () => { resolvers[1]({ items: [meme("new")], hasNext: false }) })
    await act(async () => { resolvers[0]({ items: [meme("old")], hasNext: false }) })
    expect(result.current.recent.items.map(m => m.id)).toEqual(["new"])
  })

  it("survives a StrictMode double mount and shows one consistent result", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockResolvedValue({ items: memes(3), hasNext: false }),
      getTrendingMemes: vi.fn().mockResolvedValue(trending(memes(3, "t"))),
    })
    const { result } = renderHook(() => useHomeFeeds(api), { wrapper: StrictMode })
    await waitFor(() => expect(result.current.trending.status).toBe("ready"))
    expect(vi.mocked(api.searchMemes).mock.calls.length).toBeLessThanOrEqual(2)
    expect(vi.mocked(api.getTrendingMemes).mock.calls.length).toBeLessThanOrEqual(2)
    expect(result.current.recent.items).toHaveLength(3)
    expect(result.current.trending.items).toHaveLength(3)
  })
})
