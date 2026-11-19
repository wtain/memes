import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { makeMockApi } from "../test/mockApi"
import type { Meme } from "../types/generated/all"
import { EXAMPLES_KEY, pickExampleCandidates, readCachedExamples, verifyExamples } from "./examples"

function memeWithTags(id: string, ...names: string[]): Meme {
  return { id, imageUrl: `/i/${id}`, text: [], flagged: false, tags: names.map(name => ({ name })) }
}
const threeItems = { items: [memeWithTags("a"), memeWithTags("b"), memeWithTags("c")], hasNext: false }

describe("pickExampleCandidates", () => {
  it("keeps single short tokens only", () => {
    const memes = [memeWithTags("1", "cats", "two words", "ab", "x".repeat(25), "no-way", "monday")]
    expect(pickExampleCandidates(memes).sort()).toEqual(["cats", "monday"])
  })

  it("ranks by count desc then name asc, case-insensitively, and honours max", () => {
    const memes = [
      memeWithTags("1", "Work", "zebra", "apple"),
      memeWithTags("2", "work", "zebra"),
      memeWithTags("3", "WORK", "mango"),
    ]
    expect(pickExampleCandidates(memes)).toEqual(["work", "zebra", "apple", "mango"])
    expect(pickExampleCandidates(memes, 2)).toEqual(["work", "zebra"])
  })

  it("defaults to four candidates and tolerates memes without tags", () => {
    const names = ["aaa", "bbb", "ccc", "ddd", "eee", "fff", "ggg", "hhh"]
    expect(pickExampleCandidates([memeWithTags("1", ...names), { id: "2", imageUrl: "/i/2" }])).toHaveLength(4)
  })

  it("accepts non-latin letters", () => {
    expect(pickExampleCandidates([memeWithTags("1", "кошки")])).toEqual(["кошки"])
  })
})

describe("verifyExamples", () => {
  beforeEach(() => sessionStorage.clear())
  afterEach(() => vi.restoreAllMocks())

  it("probes each candidate with limit 3 and record false and keeps the first three that return 3 items", async () => {
    const searchMemes = vi.fn().mockImplementation(async ({ query }: { query: string }) =>
      query === "bad" ? { items: [], hasNext: false } : threeItems,
    )
    const api = makeMockApi({ searchMemes })
    const out = await verifyExamples(api, ["one", "bad", "two", "three"])
    expect(out).toEqual(["one", "two", "three"])
    expect(searchMemes).toHaveBeenCalledWith({ query: "one", limit: 3, record: false })
    expect(searchMemes).toHaveBeenCalledWith({ query: "bad", limit: 3, record: false })
  })

  it("counts a rejecting probe as not verified", async () => {
    const searchMemes = vi.fn().mockImplementation(async ({ query }: { query: string }) => {
      if (query === "boom") throw new Error("down")
      return threeItems
    })
    expect(await verifyExamples(makeMockApi({ searchMemes }), ["boom", "ok1", "ok2"])).toEqual(["ok1", "ok2"])
  })

  it("returns [] for an aborted signal and caches nothing", async () => {
    const controller = new AbortController()
    controller.abort()
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue(threeItems) })
    expect(await verifyExamples(api, ["one", "two", "three"], controller.signal)).toEqual([])
    expect(sessionStorage.getItem(EXAMPLES_KEY)).toBeNull()
  })

  it("returns [] and caches an explicit negative marker when fewer than two verify", async () => {
    const api = makeMockApi({
      searchMemes: vi.fn().mockImplementation(async ({ query }: { query: string }) =>
        query === "one" ? threeItems : { items: [], hasNext: false },
      ),
    })
    expect(await verifyExamples(api, ["one", "two", "three"])).toEqual([])
    expect(JSON.parse(sessionStorage.getItem(EXAMPLES_KEY) ?? "null")).toEqual({ verified: false })
  })

  it("caches no negative marker when a probe failed (the outage may pass)", async () => {
    const api = makeMockApi({ searchMemes: vi.fn().mockRejectedValue(new Error("down")) })
    expect(await verifyExamples(api, ["one", "two"])).toEqual([])
    expect(sessionStorage.getItem(EXAMPLES_KEY)).toBeNull()
  })

  it("caches two or three verified examples", async () => {
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue(threeItems) })
    await verifyExamples(api, ["one", "two"])
    expect(JSON.parse(sessionStorage.getItem(EXAMPLES_KEY) ?? "null")).toEqual(["one", "two"])
  })

  it("does not throw when storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    const api = makeMockApi({ searchMemes: vi.fn().mockResolvedValue(threeItems) })
    expect(await verifyExamples(api, ["one", "two"])).toEqual(["one", "two"])
  })
})

describe("readCachedExamples", () => {
  beforeEach(() => sessionStorage.clear())
  afterEach(() => vi.restoreAllMocks())

  it("returns a valid cache", () => {
    sessionStorage.setItem(EXAMPLES_KEY, JSON.stringify(["cats", "dogs"]))
    expect(readCachedExamples()).toEqual(["cats", "dogs"])
  })

  it.each([
    ["missing", null],
    ["corrupt json", "nope"],
    ["wrong shape", '{"a":1}'],
    ["too many", JSON.stringify(["a", "b", "c", "d"])],
    ["too long", JSON.stringify(["x".repeat(25), "ok"])],
    ["non-string", JSON.stringify(["ok", 5])],
  ])("returns null for %s", (_label, raw) => {
    if (raw !== null) sessionStorage.setItem(EXAMPLES_KEY, raw)
    expect(readCachedExamples()).toBeNull()
  })

  it("returns null when storage throws", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    expect(readCachedExamples()).toBeNull()
  })
})
