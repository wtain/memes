import { HttpMemesApi } from "./HttpMemesApi"
import { DiscoveryRequestError } from "../../discovery/types"
import { makeDiscoveryMemes, makeDiscoveryMisses, makeDiscoveryQueries } from "../../discovery/fixtures"

const api = new HttpMemesApi("http://backend")

afterEach(() => vi.unstubAllGlobals())

function stubOk(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

describe("discovery requests", () => {
  it("sends the memes filters as camelCase query parameters, with the signal", async () => {
    const body = makeDiscoveryMemes()
    const fetchMock = stubOk(body)
    const controller = new AbortController()

    const result = await api.getDiscoveryMemes(
      { windowDays: 7, sources: ["recut", "hash_cross_corpus"], minRedrops: 2, maxImpressions: 0, sort: "saves", limit: 100 },
      controller.signal,
    )

    expect(result).toEqual(body)
    const [url, init] = fetchMock.mock.calls[0]
    const parsed = new URL(url)
    expect(parsed.origin + parsed.pathname).toBe("http://backend/api/admin/discovery/memes")
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      windowDays: "7", sources: "recut,hash_cross_corpus", minRedrops: "2", maxImpressions: "0", sort: "saves", limit: "100",
    })
    expect(init.signal).toBe(controller.signal)
  })

  it("omits undefined parameters", async () => {
    const fetchMock = stubOk(makeDiscoveryMemes())
    await api.getDiscoveryMemes({})
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/admin/discovery/memes")
  })

  it("sends the queries filters", async () => {
    const fetchMock = stubOk(makeDiscoveryQueries())
    await api.getDiscoveryQueries({ includeDegraded: true, sinceDays: 3 })
    const parsed = new URL(fetchMock.mock.calls[0][0])
    expect(parsed.pathname).toBe("/api/admin/discovery/queries")
    expect(Object.fromEntries(parsed.searchParams)).toEqual({ includeDegraded: "true", sinceDays: "3" })
  })

  it("sends the misses filters", async () => {
    const fetchMock = stubOk(makeDiscoveryMisses())
    await api.getDiscoveryMisses({ explicitOnly: true, gapMinutes: 10 })
    const parsed = new URL(fetchMock.mock.calls[0][0])
    expect(parsed.pathname).toBe("/api/admin/discovery/misses")
    expect(Object.fromEntries(parsed.searchParams)).toEqual({ explicitOnly: "true", gapMinutes: "10" })
  })

  it("turns a 422 into a DiscoveryRequestError carrying the server's message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 422, json: async () => ({ detail: "unknown popularity source(s): nope" }),
    }))
    const error = await api.getDiscoveryMemes({ sources: ["nope"] }).catch((e) => e)
    expect(error).toBeInstanceOf(DiscoveryRequestError)
    expect(error.status).toBe(422)
    expect(error.detail).toBe("unknown popularity source(s): nope")
  })

  it("keeps an empty detail when the error body is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => { throw new Error("not json") },
    }))
    const error = await api.getDiscoveryQueries({}).catch((e) => e)
    expect(error).toBeInstanceOf(DiscoveryRequestError)
    expect(error.status).toBe(500)
    expect(error.detail).toBe("")
  })
})
